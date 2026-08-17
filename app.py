
import os,re,io,json,time,uuid,base64
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from PIL import Image,ImageOps,ImageDraw,ImageFont,ImageFilter
from flask import Flask,render_template,request,send_from_directory
from dotenv import load_dotenv

load_dotenv()
APP_VERSION="V46 RENDER"
app=Flask(__name__)

# Limits for the positioned preview sent as a base64 JPEG.
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["MAX_FORM_MEMORY_SIZE"] = 16 * 1024 * 1024
app.config["MAX_FORM_PARTS"] = 100

NB_KEY=os.getenv("NEWBLACK_API_KEY","").strip()
NB_URL=os.getenv("NEWBLACK_MCP_URL","https://mcp.thenewblack.ai/api/mcp").strip()
OPENAI_KEY=os.getenv("OPENAI_API_KEY","").strip()
OPENAI_IMAGE_MODEL=os.getenv("OPENAI_IMAGE_MODEL","gpt-image-2").strip()

OUT=os.path.join(app.root_path,"static","generated")
ASSETS=os.path.join(app.root_path,"static","assets")
os.makedirs(OUT,exist_ok=True)
UA={"User-Agent":"Mozilla/5.0"}

# ---------- PRODUCT ----------
def money(t):
    if not t:return None
    m=re.search(r'(\d{1,4}(?:[.,]\d{2}))',t.replace("\xa0"," "))
    return float(m.group(1).replace(",",".")) if m else None

def fm(v):
    return f"{v:.2f}".replace(".",",")+" zł" if v is not None else ""

def scrape(url):
    r=requests.get(url,headers=UA,timeout=30);r.raise_for_status()
    s=BeautifulSoup(r.text,"html.parser")

    og=s.find("meta",property="og:title")
    title=(og.get("content","").strip() if og else "") or (s.h1.get_text(" ",strip=True) if s.h1 else "Torebka Monnari")

    cur=old=None
    for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','.current-price','.product-price','.price']:
        e=s.select_one(sel)
        if e:
            cur=money(e.get("content") or e.get_text(" ",strip=True))
            if cur is not None:break

    for sel in ['.regular-price','.old-price','.product-discount .regular-price']:
        e=s.select_one(sel)
        if e:
            old=money(e.get_text(" ",strip=True))
            if old is not None:break

    vals=[float(x.replace(",",".")) for x in re.findall(r'(\d{1,4}[.,]\d{2})\s*zł',s.get_text(" ",strip=True))]
    if cur is None and vals:cur=vals[0]
    if cur is not None and old is None:
        bigger=[x for x in vals[:20] if x>cur+1]
        if bigger:old=min(bigger)

    imgs=[]
    oi=s.find("meta",property="og:image")
    if oi and oi.get("content"):
        imgs.append(urljoin(url,oi["content"]))
    for sel in [".product-images img",".images-container img",".js-qv-product-images img",".product-cover img"]:
        for e in s.select(sel):
            src=e.get("data-image-large-src") or e.get("data-large-src") or e.get("data-src") or e.get("src")
            if src:
                u=urljoin(url,src)
                if u.startswith("http") and u not in imgs:imgs.append(u)

    if not imgs:raise RuntimeError("Nie znaleziono zdjęcia produktu.")
    disc=(old-cur) if old and cur and old>cur else None
    discount=f"-{round(disc/old*100)}%" if disc and old else (f"-{disc:.0f} zł" if disc else "PROMOCJA")


    product_description_text=""
    product_features_text=""
    try:
        desc=[]
        for sel in ["#description",".product-description",".product-description-short","[itemprop='description']",".product-information",".tabs"]:
            for node in s.select(sel):
                t=" ".join(node.get_text(" ",strip=True).split())
                if t and len(t)>20 and t not in desc:
                    desc.append(t)
        product_description_text="\n".join(desc)[:7000]

        feats=[]
        for sel in [".product-features li",".data-sheet li","table tr"]:
            for node in s.select(sel):
                t=" ".join(node.get_text(" ",strip=True).split())
                if t and 2<=len(t)<=300 and t not in feats:
                    feats.append(t)
        product_features_text="\n".join(feats[:80])[:5000]
    except Exception:
        pass

    return {"title":title,"price":fm(cur),"old_price":fm(old),"discount":discount,"images":imgs[:10],"url":url,"description":product_description_text,"features":product_features_text}

# ---------- NEW BLACK ----------
def parse_sse(t):
    last=None
    for line in (t or "").splitlines():
        line=line.strip()
        if line.startswith("data:"):
            raw=line[5:].strip()
            if raw and raw!="[DONE]":
                try:last=json.loads(raw)
                except:pass
    return last if last is not None else json.loads(t)

def deep(o,k):
    if isinstance(o,dict):
        if k in o:return o[k]
        for v in o.values():
            x=deep(v,k)
            if x is not None:return x
    elif isinstance(o,list):
        for v in o:
            x=deep(v,k)
            if x is not None:return x

def rpc(method,params,rid):
    if not NB_KEY:raise RuntimeError("Brak NEWBLACK_API_KEY w .env")
    h={"Authorization":"Bearer "+NB_KEY,"Accept":"application/json, text/event-stream","Content-Type":"application/json"}
    r=requests.post(NB_URL,headers=h,json={"jsonrpc":"2.0","id":rid,"method":method,"params":params},timeout=90)
    r.raise_for_status()
    obj=parse_sse(r.text)
    if "error" in obj:raise RuntimeError(str(obj["error"]))
    return obj

def tool(name,args,rid):
    result=rpc("tools/call",{"name":name,"arguments":args},rid).get("result",{})
    txt="\n".join([i.get("text","") for i in result.get("content",[]) if isinstance(i,dict) and i.get("type")=="text"])
    try:return json.loads(txt)
    except:return {"text":txt,"raw":result}

def init_nb():
    rpc("initialize",{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"dobratorebka-v27","version":"27.0"}},1)

def gen_lifestyle(ref, custom_prompt=""):
    default_prompt="""Create a premium photorealistic vertical fashion lifestyle photograph using the exact handbag from reference image 1.
Preserve handbag shape, proportions, color, quilting/pattern, strap/chain, hardware, zipper placement and branding as faithfully as possible.
Elegant adult female model in cream/beige tailored luxury outfit, warm European city architecture, golden-hour editorial light.
Frame model from head to below hips, handbag clearly visible, model slightly right of center.
IMPORTANT: keep the product clearly visible and preserve the exact bag details.
NO text, NO prices, NO badges, NO graphic layout."""
    prompt=(custom_prompt or "").strip() or default_prompt
    # Always reinforce product fidelity even for a custom prompt.
    prompt += "\nIMPORTANT: preserve the exact handbag from reference image 1: shape, proportions, color, pattern, straps, hardware, zipper placement and branding. Do not invent text or prices."
    g=tool("generate_image",{"prompt":prompt,"ratio":"3:4","image_1":ref},3)
    jid=deep(g,"job_id")
    if not jid:raise RuntimeError("New Black nie zwrócił job_id.")
    for i in range(45):
        time.sleep(4)
        st=tool("get_generation_status",{"job_id":str(jid)},10+i)
        state=deep(st,"status")
        if state=="done":
            u=deep(st,"image_url") or deep(st,"url")
            if not u or u in ("https:","http:"):
                raise RuntimeError("New Black zwrócił niepełny image_url.")
            return u
        if state=="error":raise RuntimeError("New Black zwrócił status error.")
    raise RuntimeError("Timeout New Black.")


# ---------- OPENAI AI TYPOGRAPHY OVERLAY ----------
def ai_typography_prompt(product):
    h1,h2,label=title_parts(product.get("title",""))
    price=product.get("price") or "SPRAWDŹ CENĘ"
    old=product.get("old_price") or ""
    discount=product.get("discount") or "PROMOCJA"

    return f"""
Create ONLY a graphic-design overlay for a premium vertical women's handbag advertisement.
The overlay will be composited over an EXISTING fashion photograph later.

ABSOLUTE SUBJECT RULE:
Do NOT generate or include a woman, face, body, handbag, clothing, architecture, street,
photographic background, product photo or scenery.
Do NOT generate any logo, logo fragment, letter A, brand mark, placeholder, dotted rectangle, or fake text in the top-left logo zone. Leave it completely empty; the application renders the logo locally.

BACKGROUND / CHROMA RULE:
Use a perfectly flat solid chroma-blue background #0000FF everywhere that should later be transparent.
Do not use blue anywhere else in the design.
No gradients, shadows, glow or texture may bleed into the blue background.

TARGET STYLE — EXACT DIRECTION:
Minimal modern luxury editorial, sleek, premium and airy, not ornate.
Palette: warm beige/ivory, black, champagne gold, and restrained fashion-magenta #F2186C.
Typography should look like a modern high-fashion campaign:
- large elegant Didot/Bodoni-style serif headline,
- expressive modern handwritten/calligraphy script in magenta,
- refined uppercase sans/serif supporting copy with generous tracking,
- clean iconography,
- strong negative space,
- no green, lime, mint or pale-green elements anywhere.

LAYOUT:
1. LEFT PANEL:
   - warm light beige/ivory panel occupying ONLY about 29-31% of width from top to just above footer,
   - one very thin warm-gold vertical border on its right edge,
   - reserve a completely EMPTY top-left logo zone, approx x=0..335, y=0..190; absolutely no letters, symbols, A-shapes, dotted frames, placeholders, boxes or marks in this zone,
   - small magenta horizontal accent line under logo,
   - large headline below logo:
       line 1 "{h1}" in ivory/white elegant serif,
       line 2 "{h2}" in warm champagne gold elegant serif,
   - elegant magenta calligraphy "Monnari" beneath headline,
   - do NOT add Must Have, feature slogans, benefit texts, benefit icons, or decorative icon rows.

2. DISCOUNT — INSIDE LEFT BEIGE PANEL:
   - small contemporary discount element fully inside the left beige panel,
   - no large black circle,
   - use a thin champagne-gold circular outline or minimal typographic badge,
   - "{discount}" is the main element, compact and modern,
   - "TANIEJ" small underneath,
   - black + restrained magenta + champagne gold,
   - clearly smaller than the headline.

3. PRICE — LOWER PART OF LEFT BEIGE PANEL:
   - make the price information extremely easy to read,
   - NO BOX, NO GREEN BACKGROUND, NO RECTANGLE, NO PLATE,
   - typography sits directly on the beige panel,
   - "TERAZ TYLKO" in small tracked ivory/white uppercase,
   - current price "{price}" very large in elegant BLACK Didot/Bodoni serif,
   - a dynamic magenta brush underline beneath the current price,
   - old price "{old}" smaller in dark gray and crossed out with one thin magenta line; never overlap the current price.

4. FOOTER:
   - slim full-width warm beige footer,
   - thin gold top line,
   - minimalist text only, NO icons,
   - keep it light, modern and low-height.

TEXT TO RENDER EXACTLY:
Headline line 1: "{h1}"
Headline line 2: "{h2}"
Script: "Monnari"

Discount: "{discount}"
Discount label: "TANIEJ"

Price label: "TERAZ TYLKO"
Current price: "{price}"
Old price: "{old}"

Footer left: "DARMOWA DOSTAWA"
Footer left subline: "OD 149 ZŁ"
Footer center: "BEZPIECZNE ZAKUPY"
Footer center subline: "GWARANCJA JAKOŚCI"
Footer right: "DOBRATOREBKA.PL"
Footer right subline: "TOREBKI DLA CIEBIE"

STRICT QUALITY RULES:
- no green anywhere,
- no duplicated price,
- no duplicated badge,
- no random extra words,
- no cropped words,
- no text over the future model's face or handbag,
- keep the entire right ~69-71% largely chroma-blue so the New Black photo stays untouched,
- typography must feel contemporary, fashion-forward and clean,
- result must look much closer to a modern premium social-media fashion ad than a classic wedding invitation.
- use MORE NEGATIVE SPACE than the previous version,
- do not fill every empty area with text or ornaments,
- keep the left panel visually light and premium,
- after the Monnari script leave generous breathing room before the two benefits,
- no benefits at all; remove TRWAŁA / I SOLIDNA and LEKKA / I WYGODNA,
- do not render "STYLOWA", "NA CO DZIEŃ", "NOWA KOLEKCJA", "TRWAŁA", "I SOLIDNA", "LEKKA", "I WYGODNA", "Must Have!" or any equivalent wording; do not render their icons.
""".strip()


def generate_ai_overlay(product):
    if not OPENAI_KEY:
        raise RuntimeError("Brak OPENAI_API_KEY w pliku .env")

    payload={
        "model": OPENAI_IMAGE_MODEL,
        "prompt": ai_typography_prompt(product),
        "size": "1024x1536",
        "quality": "medium",
        "output_format": "png",
        "n": 1
    }

    if "background" in payload:
        raise RuntimeError("V26: payload nie może zawierać parametru background.")

    headers={"Authorization":"Bearer "+OPENAI_KEY,"Content-Type":"application/json"}
    r=requests.post("https://api.openai.com/v1/images/generations",
                    headers=headers,json=payload,timeout=240)

    if not r.ok:
        try:
            detail=r.json()
        except Exception:
            detail=r.text[:1000]
        raise RuntimeError(f"{APP_VERSION} | model={OPENAI_IMAGE_MODEL} | OpenAI Image API HTTP {r.status_code}: {detail}")

    obj=r.json()
    data=(obj.get("data") or [{}])[0]
    b64=data.get("b64_json")
    if b64:
        raw=base64.b64decode(b64)
    elif data.get("url"):
        rr=requests.get(data["url"],timeout=90)
        rr.raise_for_status()
        raw=rr.content
    else:
        raise RuntimeError("OpenAI nie zwrócił obrazu nakładki.")

    overlay=Image.open(io.BytesIO(raw)).convert("RGBA")
    overlay=overlay.resize((1080,1350),Image.Resampling.LANCZOS)

    # Remove chroma blue locally.
    # Only strongly blue pixels are keyed out, preventing the previous green price-box artifact.
    px=overlay.load()
    for yy in range(overlay.height):
        for xx in range(overlay.width):
            r,g,b,a=px[xx,yy]
            blue_strength=b-max(r,g)
            if b>=150 and blue_strength>=55:
                if b>=220 and blue_strength>=120:
                    na=0
                else:
                    na=max(0,min(255,int(255*(1-(blue_strength-55)/80))))
                px[xx,yy]=(r,g,b,na)

    return overlay


def compose_ai(product,photo):
    """Preserve the New Black photograph pixel-for-pixel except for overlay pixels."""
    W,H=1080,1350
    base=photo.convert("RGBA")
    if base.size!=(W,H):
        base=ImageOps.fit(base,(W,H),Image.Resampling.LANCZOS)

    overlay=generate_ai_overlay(product)
    final=Image.alpha_composite(base,overlay)

    # Always place the user's REAL logo locally, so AI never invents/changes it.
    # A compact white logo card occupies the reserved top-left zone.
    final=paste_brand_logo(final)

    name=f"v29_ai_{uuid.uuid4().hex[:8]}.png"
    final.save(os.path.join(OUT,name),format="PNG",optimize=True)
    return name

# ---------- LOCAL LUXURY TEMPLATE ----------
def pick_font(paths):
    for p in paths:
        if os.path.exists(p):return p
    return None

DISPLAY=pick_font(["C:/Windows/Fonts/impact.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"])
SERIF=pick_font([
    "C:/Windows/Fonts/BOD_R.TTF",
    "C:/Windows/Fonts/BOD_CR.TTF",
    "C:/Windows/Fonts/BOD_CI.TTF",
    "C:/Windows/Fonts/BASKVILL.TTF",
    "C:/Windows/Fonts/pala.ttf",
    "C:/Windows/Fonts/georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
])
SERIFB=pick_font([
    "C:/Windows/Fonts/BOD_B.TTF",
    "C:/Windows/Fonts/BOD_CB.TTF",
    "C:/Windows/Fonts/BOD_BLAR.TTF",
    "C:/Windows/Fonts/BASKVILL.TTF",
    "C:/Windows/Fonts/palab.ttf",
    "C:/Windows/Fonts/georgiab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
])
DISPLAY=pick_font([
    "C:/Windows/Fonts/BOD_B.TTF",
    "C:/Windows/Fonts/BOD_CB.TTF",
    "C:/Windows/Fonts/BOD_BLAR.TTF",
    "C:/Windows/Fonts/BASKVILL.TTF",
    "C:/Windows/Fonts/georgiab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
])
SANS=pick_font([
    "C:/Windows/Fonts/aptos.ttf",
    "C:/Windows/Fonts/centurygothic.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
])
SANSB=pick_font([
    "C:/Windows/Fonts/aptosbd.ttf",
    "C:/Windows/Fonts/GOTHICB.TTF",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
])
SCRIPT=pick_font([
    "C:/Windows/Fonts/BRUSHSCI.TTF",
    "C:/Windows/Fonts/segoesc.ttf",
    "C:/Windows/Fonts/seguisbi.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaSerif-Italic.ttf"
])

def F(path,size):return ImageFont.truetype(path,size) if path else ImageFont.load_default()

def title_parts(t):
    l=t.lower()
    if "pikowan" in l and "listonosz" in l:return "PIKOWANA","LISTONOSZKA","NA ŁAŃCUSZKU"
    if "dwukomor" in l and "listonosz" in l:return "POJEMNA","LISTONOSZKA","DWUKOMOROWA"
    if "listonosz" in l:return "MODNA","LISTONOSZKA","MONNARI"
    if "shopper" in l:return "POJEMNA","SHOPPERKA","MONNARI"
    return "MODNA","TOREBKA","MONNARI"


def tracked_text(draw, xy, text, font, fill, spacing=2, anchor=None):
    """Draw text with custom letter spacing for a more editorial/luxury look."""
    x,y=xy
    widths=[]
    total=0
    for ch in text:
        bb=draw.textbbox((0,0),ch,font=font)
        w=bb[2]-bb[0]
        widths.append(w)
        total += w
    total += max(0,len(text)-1)*spacing
    if anchor=="mm":
        x -= total/2
    for i,ch in enumerate(text):
        draw.text((x,y),ch,font=font,fill=fill)
        x += widths[i] + spacing

def fit_text(d,xy,text,maxw,start,minsize,fontpath,fill):
    for s in range(start,minsize-1,-2):
        f=F(fontpath,s); bb=d.textbbox((0,0),text,font=f)
        if bb[2]-bb[0]<=maxw:
            d.text(xy,text,font=f,fill=fill);return
    d.text(xy,text,font=F(fontpath,minsize),fill=fill)

def icon(d,cx,cy,kind,gold):
    if kind=="hand":
        d.arc((cx-15,cy-10,cx+15,cy+20),200,20,fill=gold,width=2)
        d.line((cx-13,cy+8,cx+10,cy-6),fill=gold,width=2)
    elif kind=="bag":
        d.rectangle((cx-13,cy-5,cx+13,cy+16),outline=gold,width=2)
        d.arc((cx-8,cy-15,cx+8,cy+3),180,360,fill=gold,width=2)
    elif kind=="shield":
        d.polygon([(cx,cy-16),(cx+14,cy-10),(cx+10,cy+10),(cx,cy+18),(cx-10,cy+10),(cx-14,cy-10)],outline=gold)
        d.line((cx-6,cy,cx-1,cy+6,cx+8,cy-7),fill=gold,width=2)
    elif kind=="feather":
        d.arc((cx-16,cy-17,cx+16,cy+17),200,20,fill=gold,width=2)
        d.line((cx-12,cy+13,cx+12,cy-11),fill=gold,width=2)
    elif kind=="truck":
        d.rectangle((cx-24,cy-8,cx+8,cy+12),outline=gold,width=2)
        d.rectangle((cx+8,cy-2,cx+22,cy+12),outline=gold,width=2)
        d.ellipse((cx-17,cy+8,cx-8,cy+17),outline=gold,width=2)
        d.ellipse((cx+11,cy+8,cx+20,cy+17),outline=gold,width=2)
    elif kind=="globe":
        d.ellipse((cx-16,cy-16,cx+16,cy+16),outline=gold,width=2)
        d.line((cx-16,cy,cx+16,cy),fill=gold,width=2)
        d.arc((cx-7,cy-16,cx+7,cy+16),90,270,fill=gold,width=2)


def paste_brand_logo(base):
    """Render a clean text-based DobraTorebka logo locally after AI generation."""
    base = base.convert("RGBA")
    d = ImageDraw.Draw(base, "RGBA")

    # Wyczyść strefę logo po AI, aby nie zostały żadne fałszywe znaki.
    panel_bg = (247, 241, 232, 255)
    d.rectangle((0, 0, 335, 190), fill=panel_bg)

    black = (18, 18, 18, 255)
    pink = (238, 24, 101, 255)

    main_font = F(SANSB, 31)
    pink_font = F(SANSB, 58)
    sub_font = F(SANSB, 10)

    # Proste, nowoczesne logo tekstowe.
    x = 48
    d.text((x, 48), "DOBRA", font=main_font, fill=black)
    d.text((x, 82), "TOREBKA", font=main_font, fill=black)
    d.text((230, 44), "A", font=pink_font, fill=pink)
    d.text((49, 122), "OFICJALNA DYSTRYBUCJA MONNARI", font=sub_font, fill=black)

    return base


def cover(img, size):
    """Resize and center-crop an image to exactly fill size without distortion."""
    img = img.convert("RGB")
    target_w, target_h = size
    src_w, src_h = img.size

    scale = max(target_w / src_w, target_h / src_h)
    new_w = max(target_w, int(round(src_w * scale)))
    new_h = max(target_h, int(round(src_h * scale)))

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = max(0, (new_w - target_w) // 2)
    top = max(0, (new_h - target_h) // 2)
    return img.crop((left, top, left + target_w, top + target_h))


def draw_fitted_text(draw, box, text, font_path, max_size, min_size, fill):
    """Draw one-line text fitted to a bounding box."""
    x1,y1,x2,y2 = box
    text = str(text or "")
    for size in range(max_size, min_size-1, -1):
        font = F(font_path, size)
        bb = draw.textbbox((0,0), text, font=font)
        if (bb[2]-bb[0]) <= (x2-x1) and (bb[3]-bb[1]) <= (y2-y1):
            draw.text((x1,y1), text, font=font, fill=fill)
            return
    draw.text((x1,y1), text, font=F(font_path,min_size), fill=fill)


def compose(product, photo):
    W,H = 1080,1350
    beige=(246,240,230,255)
    beige2=(250,246,239,255)
    black=(22,20,18,255)
    gold=(184,143,74,255)
    magenta=(238,27,105,255)
    gray=(105,100,94,255)

    # Safe resize/crop without relying on external helper.
    src = photo.convert("RGB")
    sw,sh = src.size
    scale = max(W/sw, H/sh)
    nw,nh = max(W,int(sw*scale)), max(H,int(sh*scale))
    src = src.resize((nw,nh), Image.Resampling.LANCZOS)
    left=(nw-W)//2
    top=(nh-H)//2
    base = src.crop((left,top,left+W,top+H)).convert("RGBA")

    d = ImageDraw.Draw(base,"RGBA")
    panel_w=300
    footer_y=1232

    # Fixed beige panel.
    d.rectangle((0,0,panel_w,footer_y), fill=beige)
    d.line((panel_w,0,panel_w,footer_y), fill=gold, width=2)

    # Real logo from local asset.
    logo_path=os.path.join(ASSETS,"logo_dobratorebka_v29.png")
    if os.path.exists(logo_path):
        logo=Image.open(logo_path).convert("RGBA")
        logo=ImageOps.contain(logo,(238,90),Image.Resampling.LANCZOS)
        base.alpha_composite(logo,(30,38))
        d=ImageDraw.Draw(base,"RGBA")

    # Product name logic without old helper dependency.
    title=(product.get("title") or "").lower()
    if "listonosz" in title:
        h1,h2="MODNA","LISTONOSZKA"
    elif "shopper" in title:
        h1,h2="MODNA","SHOPPERKA"
    else:
        h1,h2="MODNA","TOREBKA"

    d.text((34,235),h1,font=F(SERIF,39),fill=black)
    d.text((34,283),h2,font=F(SERIF,39),fill=black)
    d.text((34,343),"Monnari",font=F(SCRIPT,38),fill=magenta)

    # Small discount badge.
    discount=product.get("discount") or "PROMOCJA"
    cx,cy,r=150,535,58
    d.ellipse((cx-r,cy-r,cx+r,cy+r), outline=gold, width=2)

    # simple local text fit
    def draw_fit(box,text,font_path,max_size,min_size,fill):
        x1,y1,x2,y2=box
        for size in range(max_size,min_size-1,-1):
            ft=F(font_path,size)
            bb=d.textbbox((0,0),str(text),font=ft)
            if bb[2]-bb[0] <= x2-x1 and bb[3]-bb[1] <= y2-y1:
                d.text((x1,y1),str(text),font=ft,fill=fill)
                return
        d.text((x1,y1),str(text),font=F(font_path,min_size),fill=fill)

    draw_fit((102,507,198,557),discount,SERIF,32,22,black)
    d.text((123,563),"TANIEJ",font=F(SANSB,11),fill=black)
    d.line((130,585,170,585),fill=magenta,width=3)

    # Price.
    price=product.get("price") or "SPRAWDŹ CENĘ"
    old=product.get("old_price") or ""
    d.text((34,820),"TERAZ TYLKO",font=F(SANSB,14),fill=black)
    draw_fit((30,852,274,930),price,SERIF,54,30,black)
    d.line((34,945,265,945),fill=magenta,width=3)

    if old:
        draw_fit((46,968,250,1012),old,SERIF,26,18,gray)
        d.line((44,993,250,993),fill=magenta,width=3)

    # Footer.
    d.rectangle((0,footer_y,W,H),fill=beige2)
    d.line((0,footer_y,W,footer_y),fill=gold,width=2)
    d.line((350,1250,350,1320),fill=gold,width=1)
    d.line((705,1250,705,1320),fill=gold,width=1)

    d.text((42,1265),"DARMOWA DOSTAWA",font=F(SANSB,14),fill=black)
    d.text((42,1292),"OD 149 ZŁ",font=F(SANS,12),fill=magenta)
    d.text((390,1265),"BEZPIECZNE ZAKUPY",font=F(SANSB,14),fill=black)
    d.text((390,1292),"GWARANCJA JAKOŚCI",font=F(SANS,12),fill=gray)
    d.text((748,1265),"DOBRATOREBKA.PL",font=F(SANSB,14),fill=magenta)
    d.text((748,1292),"TOREBKI DLA CIEBIE",font=F(SANS,12),fill=black)

    name=f"v30_{uuid.uuid4().hex[:8]}.jpg"
    base.convert("RGB").save(os.path.join(OUT,name),quality=96)
    return name


def decode_positioned(data_url):
    raw=data_url.split(",",1)[1]
    im=Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
    return im.resize((1080,1350),Image.Resampling.LANCZOS) if im.size!=(1080,1350) else im

# ---------- ROUTES ----------

def log_exception(prefix, exc):
    try:
        import traceback
        with open("error_log.txt","a",encoding="utf-8") as f:
            f.write("\n"+"="*70+"\n")
            f.write(prefix+"\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass


def prepare_source_preview(image_url):
    """Download a shop product image and prepare the same 1080x1350 positioning preview as New Black."""
    r=requests.get(image_url,headers=UA,timeout=60)
    r.raise_for_status()
    img=Image.open(io.BytesIO(r.content)).convert("RGB")

    # Do not invent/change the product image. Only fit it to the working canvas.
    bg=Image.new("RGB",(1080,1350),(255,255,255))
    contained=ImageOps.contain(img,(900,1120),Image.Resampling.LANCZOS)
    x=(1080-contained.width)//2
    y=(1350-contained.height)//2
    bg.paste(contained,(x,y))

    name=f"source_{uuid.uuid4().hex[:8]}.jpg"
    bg.save(os.path.join(OUT,name),quality=97)
    return name


def compose_light_logo_price(product, photo):
    """
    V38:
    - full-width white header,
    - elegant DOBRATOREBKA wordmark,
    - no beige background,
    - source/New Black photo below,
    - only current + old price.
    """
    W,H = 1080,1350

    # Keep the selected photo as the dominant visual.
    src = photo.convert("RGB")
    if src.size != (W,H):
        src = ImageOps.fit(src,(W,H),Image.Resampling.LANCZOS,centering=(0.5,0.5))
    base = src.convert("RGBA")
    d = ImageDraw.Draw(base,"RGBA")

    white=(255,255,255,255)
    black=(16,16,16,255)
    gray=(110,110,110,255)
    pink=(230,30,96,255)

    # ---------- WHITE HEADER ----------
    header_h = 82
    d.rectangle((0,0,W,header_h),fill=white)

    # Elegant editorial serif. Existing font picker will use the best available serif.
    # Increase tracking manually to create a premium fashion wordmark.
    word = "D O B R A T O R E B K A . P L"
    font_size = 46
    while font_size > 24:
        font = F(SERIFB, font_size)
        bb_test = d.textbbox((0,0), word, font=font)
        if (bb_test[2]-bb_test[0]) <= W-56:
            break
        font_size -= 1

    def tracked_width(text, font, spacing):
        total=0
        for i,ch in enumerate(text):
            bb=d.textbbox((0,0),ch,font=font)
            total += bb[2]-bb[0]
            if i < len(text)-1:
                total += spacing
        return total

    def draw_tracked_centered(text, y, font, fill, spacing):
        total = tracked_width(text,font,spacing)
        x = (W-total)/2
        for i,ch in enumerate(text):
            d.text((x,y),ch,font=font,fill=fill)
            bb=d.textbbox((0,0),ch,font=font)
            x += (bb[2]-bb[0]) + (spacing if i < len(text)-1 else 0)

    # Make the name span most of the width, but keep safe margins.
    spacing=8
    while tracked_width(word,font,spacing) > W-120 and spacing > 1:
        spacing -= 1

    bb = d.textbbox((0,0),word,font=font)
    tx = (W - (bb[2]-bb[0])) // 2
    text_h = bb[3]-bb[1]
    ty = max(5, (header_h-text_h)//2 - 2)
    d.text((tx,ty),word,font=font,fill=black)

    # Tiny premium underline/accent only; no beige.

    # ---------- PRICE ----------
    price=str(product.get("price") or "").strip() or "SPRAWDŹ CENĘ"
    old=str(product.get("old_price") or "").strip()

    # Clean white price card in lower-right.
    card_w=390
    card_h=190 if old else 135
    card_x=W-card_w-36
    card_y=H-card_h-36

    d.rounded_rectangle(
        (card_x,card_y,card_x+card_w,card_y+card_h),
        radius=26,
        fill=(255,255,255,255)
    )

    # Current price: elegant large serif.
    size=60
    while size>30:
        price_font=F(SERIFB,size)
        bb=d.textbbox((0,0),price,font=price_font)
        if bb[2]-bb[0] <= card_w-48:
            break
        size-=2

    d.text((card_x+24,card_y+18),price,font=price_font,fill=black)
    d.line((card_x+24,card_y+95,card_x+card_w-24,card_y+95),fill=pink,width=3)

    if old:
        old_font=F(SERIF,27)
        d.text((card_x+26,card_y+121),old,font=old_font,fill=gray)
        bb=d.textbbox((card_x+26,card_y+121),old,font=old_font)
        yy=(bb[1]+bb[3])//2
        d.line((bb[0]-3,yy,bb[2]+3,yy),fill=pink,width=3)


    # ---------- ORIGINAL MONNARI LOGO ----------
    # Uses the user's attachment 1:1. No AI redraw/reconstruction.
    monnari_path = os.path.join("static","assets","monnari_logo_original.png")
    if os.path.exists(monnari_path):
        monnari = Image.open(monnari_path).convert("RGBA")

        # Scale proportionally only; never stretch.
        target_w = 285
        scale = target_w / monnari.width
        target_h = max(1, int(round(monnari.height * scale)))
        monnari = monnari.resize((target_w,target_h),Image.Resampling.LANCZOS)

        # Lower-left safe margin.
        mx = 38
        my = H - monnari.height - 38
        base.alpha_composite(monnari,(mx,my))

    name=f"v40_{uuid.uuid4().hex[:8]}.jpg"
    base.convert("RGB").save(os.path.join(OUT,name),quality=96)
    return name


def prepare_source_photo(ref):
    r=requests.get(ref,headers=UA,timeout=60)
    r.raise_for_status()
    photo=Image.open(io.BytesIO(r.content)).convert("RGB")
    canvas=Image.new("RGB",(1080,1350),(255,255,255))
    photo = ImageOps.fit(
        photo,
        (1080,1350),
        Image.Resampling.LANCZOS,
        centering=(0.5,0.5)
    )
    name=f"source_{uuid.uuid4().hex[:8]}.jpg"
    photo.save(os.path.join(OUT,name),quality=97)
    return name





def _post_facts(product):
    facts=[]
    description=" ".join((product.get("description") or "").split())
    if description:
        for part in re.split(r'(?<=[.!?])\s+',description):
            part=part.strip(" •-")
            if 20<=len(part)<=240 and part not in facts:
                facts.append(part)

    for part in (product.get("features") or "").splitlines():
        part=" ".join(part.split()).strip(" •-")
        if 3<=len(part)<=180 and part not in facts:
            facts.append(part)

    return facts[:8]

def build_facebook_post(product,style="facebook_emoji",
                        add_features=True,add_shipping=True,
                        add_hashtags=True,add_emojis=True):
    title=(product.get("title") or "Torebka Monnari").strip()
    price=(product.get("price") or "").strip()
    old=(product.get("old_price") or "").strip()
    url=(product.get("url") or "https://dobratorebka.pl").strip()
    facts=_post_facts(product)

    f1=facts[0] if len(facts)>0 else ""
    f2=facts[1] if len(facts)>1 else ""
    f3=facts[2] if len(facts)>2 else ""

    bag="👜 " if add_emojis else ""
    spark="✨ " if add_emojis else ""
    fire="🔥 " if add_emojis else ""
    cart="🛒 " if add_emojis else ""
    ship="🚚 " if add_emojis else ""

    if price and old and old!=price:
        price_line=f"{fire}Teraz {price} zamiast {old}"
    elif price:
        price_line=f"{fire}Cena: {price}"
    else:
        price_line=""

    lines=[]

    if style=="short":
        lines.append(f"{bag}{title}")
        if f1: lines.append(f1)

    elif style=="premium":
        lines.append(title)
        if f1: lines.append(f"{spark}{f1}")
        if add_features and f2: lines.append(f2)

    elif style=="sales":
        lines.append(f"{fire}{title}")
        if add_features:
            for x in [f1,f2,f3]:
                if x: lines.append(f"• {x}")

    elif style=="storytelling":
        lines.append(f"{bag}{title}")
        for x in [f1,f2]:
            if x: lines.append(x)
        if add_features and f3: lines.append(f3)

    elif style=="instagram":
        lines.append(f"{spark}{title}")
        if f1: lines.append(f1)

    else:
        lines.append(f"{bag}{title}")
        if f1: lines.append(f"{spark}{f1}")
        if add_features:
            for x in [f2,f3]:
                if x: lines.append(f"• {x}")

    if price_line:
        lines += ["",price_line]

    if add_shipping:
        lines.append(f"{ship}Darmowa dostawa od 149 zł")

    lines += ["",f"{cart}Sprawdź produkt:",url]

    if add_hashtags:
        lines += ["","#DobraTorebka #Monnari #TorebkaDamska #ModaDamska"]

    return "\n".join(lines)

def build_5_posts(product):
    return [
        ("Krótki",build_facebook_post(product,"short")),
        ("Premium",build_facebook_post(product,"premium")),
        ("Sprzedażowy",build_facebook_post(product,"sales")),
        ("Storytelling",build_facebook_post(product,"storytelling")),
        ("Instagram",build_facebook_post(product,"instagram")),
    ]



def publish_to_facebook_page(image_filename, caption):
    """
    Publish the generated image + caption to a Facebook Page.
    Credentials are read from environment variables:
      FB_PAGE_ID
      FB_PAGE_ACCESS_TOKEN
      FB_GRAPH_VERSION (optional; e.g. v23.0). If empty, unversioned Graph endpoint is used.
    """
    page_id = (os.getenv("FB_PAGE_ID") or "").strip()
    token = (os.getenv("FB_PAGE_ACCESS_TOKEN") or "").strip()
    graph_version = (os.getenv("FB_GRAPH_VERSION") or "").strip()

    if not page_id:
        raise RuntimeError("Brak FB_PAGE_ID w pliku .env.")
    if not token:
        raise RuntimeError("Brak FB_PAGE_ACCESS_TOKEN w pliku .env.")

    image_path = os.path.join(OUT, image_filename)
    if not os.path.exists(image_path):
        raise RuntimeError("Nie znaleziono wygenerowanej grafiki do publikacji.")

    if graph_version:
        base = f"https://graph.facebook.com/{graph_version}"
    else:
        base = "https://graph.facebook.com"

    endpoint = f"{base}/{page_id}/photos"

    with open(image_path, "rb") as f:
        files = {"source": (os.path.basename(image_path), f, "image/jpeg")}
        data = {
            "caption": caption or "",
            "access_token": token,
        }
        r = requests.post(endpoint, data=data, files=files, timeout=120)

    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}

    if not r.ok:
        msg = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
        raise RuntimeError(f"Facebook API HTTP {r.status_code}: {msg or payload}")

    return payload




def test_facebook_connection():
    page_id = (os.getenv("FB_PAGE_ID") or "").strip()
    token = (os.getenv("FB_PAGE_ACCESS_TOKEN") or "").strip()
    graph_version = (os.getenv("FB_GRAPH_VERSION") or "").strip()

    checks = []
    result = {"ok": False, "checks": checks, "token_type": "unknown"}

    if not page_id:
        checks.append({"ok": False, "label": "FB_PAGE_ID", "detail": "Brak FB_PAGE_ID w pliku .env."})
        return result
    checks.append({"ok": True, "label": "FB_PAGE_ID", "detail": page_id})

    if not token:
        checks.append({"ok": False, "label": "FB_PAGE_ACCESS_TOKEN", "detail": "Brak tokenu w pliku .env."})
        return result
    checks.append({"ok": True, "label": "FB_PAGE_ACCESS_TOKEN", "detail": "Token jest wpisany."})

    base = f"https://graph.facebook.com/{graph_version}" if graph_version else "https://graph.facebook.com"

    # 1. Identify token owner.
    r = requests.get(
        f"{base}/me",
        params={"access_token": token, "fields": "id,name"},
        timeout=30
    )
    try:
        me = r.json()
    except Exception:
        me = {"raw": r.text}

    if not r.ok:
        msg = me.get("error", {}).get("message") if isinstance(me, dict) else str(me)
        checks.append({"ok": False, "label": "Token /me", "detail": f"HTTP {r.status_code}: {msg}"})
        return result

    token_id = str(me.get("id",""))
    token_name = me.get("name","")

    if token_id == page_id:
        result["token_type"] = "page"
        checks.append({
            "ok": True,
            "label": "Typ tokenu",
            "detail": f"Page Access Token: {token_name or page_id}"
        })
        checks.append({
            "ok": True,
            "label": "Uprawnienia",
            "detail": "Page Access Token rozpoznany. Pomijam test /me/permissions, który może zwracać fałszywy błąd dla tokenu strony."
        })
    else:
        result["token_type"] = "user_or_other"
        checks.append({
            "ok": False,
            "label": "Typ tokenu",
            "detail": f"/me zwraca {token_name or token_id} ({token_id}), a FB_PAGE_ID to {page_id}."
        })

    # 2. Confirm access to configured Page.
    rr = requests.get(
        f"{base}/{page_id}",
        params={"access_token": token, "fields": "id,name"},
        timeout=30
    )
    try:
        page_data = rr.json()
    except Exception:
        page_data = {"raw": rr.text}

    if rr.ok:
        checks.append({
            "ok": True,
            "label": "Dostęp do strony",
            "detail": f"{page_data.get('name','')} ({page_data.get('id',page_id)})"
        })
    else:
        msg = page_data.get("error", {}).get("message") if isinstance(page_data, dict) else str(page_data)
        checks.append({"ok": False, "label": "Dostęp do strony", "detail": f"HTTP {rr.status_code}: {msg}"})

    result["ok"] = (
        result.get("token_type") == "page"
        and any(c.get("label") == "Dostęp do strony" and c.get("ok") for c in checks)
    )
    return result

def test_facebook_publish_capability():
    diag = test_facebook_connection()
    if diag.get("ok"):
        return {
            "ok": True,
            "detail": "Page Access Token i dostęp do strony wyglądają poprawnie. Możesz spróbować publikacji.",
            "diagnostics": diag,
        }
    return {
        "ok": False,
        "detail": "Konfiguracja Facebook nie przeszła diagnostyki. Publikacja nie została wykonana.",
        "diagnostics": diag,
    }


@app.route("/",methods=["GET","POST"])
def index():
    stage="start"
    data=None
    error=None

    if request.method=="POST":
        action=request.form.get("action","load")
        try:
            if action=="load":
                url=request.form.get("url","").strip()
                if not url:
                    raise RuntimeError("Wklej link do produktu.")

                product=scrape(url)
                product["url"]=url
                images=product.get("images") or []
                if not images:
                    raise RuntimeError("Nie znaleziono zdjęć w ofercie.")

                data={
                    "url":url,
                    "product":product,
                    "product_json":json.dumps(product,ensure_ascii=False),
                    "images":images[:12],
                }
                stage="choose"

            elif action=="prepare":
                product=json.loads(request.form.get("product_json","{}"))
                source_mode=request.form.get("source_mode","source")
                image_index=max(1,int(request.form.get("image_index","1") or "1"))
                newblack_prompt=request.form.get("newblack_prompt","").strip()
                nb_count=max(1,min(4,int(request.form.get("nb_count","1") or "1")))

                images=product.get("images") or []
                if image_index>len(images):
                    raise RuntimeError(f"Wykryto tylko {len(images)} zdjęć produktu.")
                ref=images[image_index-1]

                if source_mode=="newblack":
                    init_nb()

                    generated=[]
                    for i in range(nb_count):
                        life_url=gen_lifestyle(ref,newblack_prompt)
                        rr=requests.get(life_url,timeout=60)
                        rr.raise_for_status()
                        photo=Image.open(io.BytesIO(rr.content)).convert("RGB")
                        photo=ImageOps.fit(
                            photo,(1080,1350),Image.Resampling.LANCZOS,
                            centering=(0.5,0.47)
                        )
                        preview_name=f"nb_{uuid.uuid4().hex[:8]}_{i+1}.jpg"
                        photo.save(os.path.join(OUT,preview_name),quality=97)
                        generated.append(preview_name)

                    if len(generated)>1:
                        data={
                            "product_json":json.dumps(product,ensure_ascii=False),
                            "source_mode":source_mode,
                            "source_label":f"NEW BLACK — {len(generated)} warianty",
                            "newblack_prompt":newblack_prompt,
                            "variants":generated,
                        }
                        stage="choose_variant"
                    else:
                        data={
                            "product_json":json.dumps(product,ensure_ascii=False),
                            "preview_name":generated[0],
                            "source_mode":source_mode,
                            "source_label":"NEW BLACK — zdjęcie lifestyle",
                            "newblack_prompt":newblack_prompt,
                        }
                        stage="preview"

                else:
                    preview_name=prepare_source_photo(ref)
                    data={
                        "product_json":json.dumps(product,ensure_ascii=False),
                        "preview_name":preview_name,
                        "source_mode":source_mode,
                        "source_label":f"ZDJĘCIE Z OFERTY NR {image_index} — 0 kredytów New Black",
                        "newblack_prompt":"",
                    }
                    stage="preview"

            elif action=="select_variant":
                product=json.loads(request.form.get("product_json","{}"))
                selected=request.form.get("selected_variant","")
                if not selected or not os.path.exists(os.path.join(OUT,selected)):
                    raise RuntimeError("Nie znaleziono wybranego wariantu New Black.")

                data={
                    "product_json":json.dumps(product,ensure_ascii=False),
                    "preview_name":selected,
                    "source_mode":"newblack",
                    "source_label":"NEW BLACK — wybrany wariant",
                    "newblack_prompt":request.form.get("newblack_prompt",""),
                }
                stage="preview"

            elif action=="final":
                product=json.loads(request.form.get("product_json","{}"))
                preview_name=request.form.get("preview_name","")
                path=os.path.join(OUT,preview_name)

                if not os.path.exists(path):
                    raise RuntimeError("Nie znaleziono zdjęcia podglądowego.")

                photo=Image.open(path).convert("RGB")
                final=compose_light_logo_price(product,photo)

                style="facebook_emoji"
                add_features=True
                add_shipping=True
                add_hashtags=True
                add_emojis=True

                data={
                    "final":final,
                    "product_json":json.dumps(product,ensure_ascii=False),
                    "facebook_post":build_facebook_post(product,style,add_features,add_shipping,add_hashtags,add_emojis),
                    "post_style":style,
                    "add_features":add_features,
                    "add_shipping":add_shipping,
                    "add_hashtags":add_hashtags,
                    "add_emojis":add_emojis,
                    "five_posts":build_5_posts(product)
                }
                stage="done"

            elif action=="regenerate_post":
                product=json.loads(request.form.get("product_json","{}"))
                final=request.form.get("final","")
                style=request.form.get("post_style","facebook_emoji")
                add_features=request.form.get("add_features")=="1"
                add_shipping=request.form.get("add_shipping")=="1"
                add_hashtags=request.form.get("add_hashtags")=="1"
                add_emojis=request.form.get("add_emojis")=="1"

                data={
                    "final":final,
                    "product_json":json.dumps(product,ensure_ascii=False),
                    "facebook_post":build_facebook_post(product,style,add_features,add_shipping,add_hashtags,add_emojis),
                    "post_style":style,
                    "add_features":add_features,
                    "add_shipping":add_shipping,
                    "add_hashtags":add_hashtags,
                    "add_emojis":add_emojis,
                    "five_posts":build_5_posts(product)
                }
                stage="done"

            elif action=="test_facebook":
                product=json.loads(request.form.get("product_json","{}"))
                final=request.form.get("final","")
                caption=request.form.get("facebook_post","")
                fb_test=test_facebook_connection()

                data={
                    "final":final,
                    "product_json":json.dumps(product,ensure_ascii=False),
                    "facebook_post":caption,
                    "post_style":request.form.get("post_style","facebook_emoji"),
                    "add_features":request.form.get("add_features")=="1",
                    "add_shipping":request.form.get("add_shipping")=="1",
                    "add_hashtags":request.form.get("add_hashtags")=="1",
                    "add_emojis":request.form.get("add_emojis")=="1",
                    "five_posts":build_5_posts(product),
                    "facebook_test":fb_test,
                }
                stage="done"

            elif action=="test_publish_capability":
                product=json.loads(request.form.get("product_json","{}"))
                final=request.form.get("final","")
                caption=request.form.get("facebook_post","")
                fb_capability=test_facebook_publish_capability()

                data={
                    "final":final,
                    "product_json":json.dumps(product,ensure_ascii=False),
                    "facebook_post":caption,
                    "post_style":request.form.get("post_style","facebook_emoji"),
                    "add_features":request.form.get("add_features")=="1",
                    "add_shipping":request.form.get("add_shipping")=="1",
                    "add_hashtags":request.form.get("add_hashtags")=="1",
                    "add_emojis":request.form.get("add_emojis")=="1",
                    "five_posts":build_5_posts(product),
                    "facebook_test":fb_capability.get("diagnostics"),
                    "facebook_capability":fb_capability,
                }
                stage="done"

            elif action=="publish_facebook":
                product=json.loads(request.form.get("product_json","{}"))
                final=request.form.get("final","")
                caption=request.form.get("facebook_post","")
                result=publish_to_facebook_page(final,caption)

                data={
                    "final":final,
                    "product_json":json.dumps(product,ensure_ascii=False),
                    "facebook_post":caption,
                    "post_style":request.form.get("post_style","facebook_emoji"),
                    "add_features":request.form.get("add_features")=="1",
                    "add_shipping":request.form.get("add_shipping")=="1",
                    "add_hashtags":request.form.get("add_hashtags")=="1",
                    "add_emojis":request.form.get("add_emojis")=="1",
                    "five_posts":build_5_posts(product),
                    "facebook_publish_ok":True,
                    "facebook_publish_result":result,
                }
                stage="done"

        except Exception as e:
            try:
                log_exception("Błąd V45",e)
            except Exception:
                pass
            error=str(e)

    return render_template("index.html",stage=stage,data=data,error=error)



@app.route("/healthz")
def healthz():
    return {"status": "ok", "app": APP_VERSION}, 200


@app.route("/generated/<path:n>")
def generated(n):return send_from_directory(OUT,n)

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5041")), debug=False)
