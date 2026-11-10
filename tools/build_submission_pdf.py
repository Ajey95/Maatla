"""Create Maatla's one-page Gnani challenge submission brief."""
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "outputs" / "Maatla-submission-one-page.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)

pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", r"C:\Windows\Fonts\arialbd.ttf"))

INK = colors.HexColor("#102D35")
GREEN = colors.HexColor("#075E53")
MINT = colors.HexColor("#EAF5F1")
SAND = colors.HexColor("#F7F1E6")
GOLD = colors.HexColor("#D3A35D")
MUTED = colors.HexColor("#52646A")
RULE = colors.HexColor("#DCE5E3")
WHITE = colors.white

W, H = A4
c = canvas.Canvas(str(OUT), pagesize=A4, pageCompression=1)
c.setTitle("Maatla | Gnani AI Internship Challenge | One-page project brief")
c.setAuthor("Jashwanth and Rohith | Maatla project team")
c.setSubject("Project explanation, Gnani API usage, contribution and submission details")

body = ParagraphStyle("body", fontName="Arial", fontSize=8.35, leading=11.35,
                      textColor=INK, spaceAfter=0)
small = ParagraphStyle("small", parent=body, fontSize=7.6, leading=10.35)
header = ParagraphStyle("header", parent=body, fontName="Arial-Bold", fontSize=9.2,
                        leading=12, textColor=GREEN)


def para(text, x, y, width, style=body, gap=7):
    p = Paragraph(text, style)
    _, height = p.wrap(width, H)
    p.drawOn(c, x, y - height)
    return y - height - gap


def section(title, text, x, y, width):
    c.setFillColor(GREEN)
    c.roundRect(x, y - 2, 4, 13, 2, stroke=0, fill=1)
    y = para(title, x + 11, y + 10, width - 11, header, gap=2)
    return para(text, x, y, width, body, gap=13)


# Header
c.setFillColor(INK)
c.rect(0, H - 103, W, 103, stroke=0, fill=1)
c.setFillColor(GOLD)
c.roundRect(34, H - 43, 61, 3, 1.5, stroke=0, fill=1)
c.setFillColor(WHITE)
c.setFont("Arial-Bold", 21)
c.drawString(34, H - 35, "MAATLA")
c.setFont("Arial", 9.2)
c.drawString(35, H - 61, "Live cross-language land-sales copilot")
c.setFillColor(colors.HexColor("#BFD7D0"))
c.setFont("Arial", 8.6)
c.drawString(35, H - 80, "Telugu seller  /  Hinglish buyer  /  Human-approved voice reply")
c.setFillColor(GOLD)
c.roundRect(W - 172, H - 67, 138, 24, 12, stroke=0, fill=1)
c.setFillColor(INK)
c.setFont("Arial-Bold", 8.2)
c.drawCentredString(W - 103, H - 58, "GNANI AI CHALLENGE")

# One-line value and visual process
margin = 34
y = H - 119
y = para("<b>Purpose.</b> Help a human land seller understand a Hindi-English buyer in real time, respond in the buyer's language, verify claims, and carry the client's context into the next call.",
         margin, y, W - margin * 2, body, gap=12)

steps = [
    ("BUYER", "Hinglish speech"),
    ("PRISMA", "Code-mixed STT"),
    ("SELLER", "Telugu cue + review"),
    ("TIMBRE", "Approved Hinglish"),
    ("BUYER", "Voice + caption"),
]
box_w, box_h, gap_x = 91, 45, 17
start_x = (W - (box_w * 5 + gap_x * 4)) / 2
for i, (label, detail) in enumerate(steps):
    x = start_x + i * (box_w + gap_x)
    c.setFillColor(MINT if i in (1, 3) else SAND)
    c.roundRect(x, y - box_h, box_w, box_h, 7, stroke=0, fill=1)
    c.setFillColor(GREEN)
    c.setFont("Arial-Bold", 8.2)
    c.drawCentredString(x + box_w / 2, y - 17, label)
    c.setFillColor(MUTED)
    c.setFont("Arial", 6.85)
    c.drawCentredString(x + box_w / 2, y - 31, detail)
    if i < 4:
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.4)
        c.line(x + box_w + 3, y - 22, x + box_w + gap_x - 4, y - 22)
        c.line(x + box_w + gap_x - 7, y - 19, x + box_w + gap_x - 4, y - 22)
        c.line(x + box_w + gap_x - 7, y - 25, x + box_w + gap_x - 4, y - 22)
y -= box_h + 20

col_gap = 24
col_w = (W - 2 * margin - col_gap) / 2
left_x, right_x = margin, margin + col_w + col_gap
c.setStrokeColor(RULE)
c.line(W / 2, 98, W / 2, y + 3)

ly = y
ly = section("01  Problem and users",
    "A Telugu-speaking seller in Telangana or Andhra Pradesh may meet a buyer who prefers Hindi-English. Budget, plot size, document questions, and visit plans can be missed or miscommunicated during a live call. <b>Primary user:</b> the human land advisor. <b>Second user:</b> the buyer, who needs a natural Hinglish response and a simple call view.",
    left_x, ly, col_w)
ly = section("02  Working application",
    "The two people join separate browser views with consent and speaker-labelled captions. A Prisma segment triggers a provisional private Telugu text gist. Related segments form one turn, producing a complete cue and one suggested next move. The seller edits a Hinglish draft, checks the Telugu meaning of that exact text, and approves it before the buyer hears Timbre speech. The same workspace checks knowledge and official sources, records reviewed facts and commitments, and restores buyer context in a follow-up call.",
    left_x, ly, col_w)
ly = section("03  Knowledge checks and follow-up",
    "Approved, unexpired seller knowledge is searched locally with its source label and date. The seller can fetch a current HTTPS page from allowlisted official domains; optional Brave web search is restricted to those domains when a key is configured. The recorded run used an official-page fetch, not Brave search. A source is not proof of a particular plot's title or access. Seller-approved facts retain their originating buyer statement and validity history; open promises and recent turns reappear in the next call. This keeps the human seller informed without inventing claims.",
    left_x, ly, col_w)

ry = y
ry = section("04  Gnani AI technology used",
    "<b>Prisma v3 streaming STT:</b> separate server-side WebSocket sessions for seller and buyer; browser audio is sent as 16 kHz mono PCM frames. Hindi and Telugu locale hints plus <b>codemix</b> support mixed speech. Prisma transcript segments drive private coaching, captions, and source-linked memory.<br/><br/><b>Timbre v2.5 TTS:</b> the server sends only the seller-approved reply with <b>hi-en / Poorvi</b> and delivers the returned WAV to the buyer. Optional seller-only Telugu audio uses <b>te-IN / Varuni</b>. The normal Telugu cue is text, so coaching does not wait for TTS. Credentials remain on the backend.",
    right_x, ry, col_w)
ry = section("05  Other technology and evidence",
    "FastAPI, React, WebRTC and SQLite implement the call and dated client context. Local Ollama models handle reasoning and the edited-draft Telugu preview; <b>Gnani Evon was not used</b>. A synthetic two-speaker recording exercised 3 buyer turns, 3 complete Telugu cues, and 3 seller-approved Timbre replies through live Gnani APIs, without voice overlap. This is a prototype demo, not a live-human accuracy study or a deployed telephony service.",
    right_x, ry, col_w)
ry = section("06  Team and contribution",
    "<b>Project team: Jashwanth and Rohith.</b> The product concept, language workflow, requirements, and review direction came from the project team. Implementation was AI-assisted with Codex. Each member should state their own actual responsibilities in the submission form; this brief does not invent a division of work.",
    right_x, ry, col_w)

if min(ly, ry) < 270:
    raise RuntimeError(f"One-page content exceeds footer: left={ly:.1f}, right={ry:.1f}")

# A compact real product capture anchors the process diagram in the UI.
card_y, card_h = 102, 160
c.setFillColor(colors.HexColor("#F2F6F4"))
c.roundRect(margin, card_y, W - 2 * margin, card_h, 9, stroke=0, fill=1)
c.drawImage(str(ROOT / "work" / "maatla" / "docs" / "assets" / "reviewed-reply.png"),
            margin, card_y, width=284, height=160, preserveAspectRatio=True, anchor="c")
c.setFillColor(INK)
c.roundRect(margin + 298, card_y + 12, W - 2 * margin - 310, card_h - 24,
            8, stroke=0, fill=1)
c.setFillColor(GOLD)
c.setFont("Arial-Bold", 7.7)
c.drawString(margin + 311, card_y + 128, "RECORDED PRODUCT VIEW")
c.setFillColor(WHITE)
c.setFont("Arial-Bold", 9.1)
c.drawString(margin + 311, card_y + 105, "Two separate call views")
c.setFont("Arial", 8.3)
c.drawString(margin + 311, card_y + 83, "Private Telugu seller cue")
c.drawString(margin + 311, card_y + 65, "Reviewed Hinglish reply")
c.drawString(margin + 311, card_y + 47, "Buyer-facing Timbre voice")
c.setFillColor(colors.HexColor("#BFD7D0"))
c.setFont("Arial", 7.3)
c.drawString(margin + 311, card_y + 28, "Synthetic buyer scenario")

c.setStrokeColor(RULE)
c.line(margin, 86, W - margin, 86)
c.setFillColor(MUTED)
c.setFont("Arial", 7.4)
c.drawString(margin, 70, "Submission brief  |  Working local prototype  |  Synthetic buyer and listing data")
c.drawString(margin, 55, "Full architecture, verification, setup and visual walkthrough: project README")
c.setFillColor(GREEN)
c.setFont("Arial-Bold", 7.4)
c.drawRightString(W - margin, 55, "1 / 1")
c.showPage()
c.save()
print(OUT)
