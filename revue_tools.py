"""Massey Law Review : citations, métadonnées d'articles et PDF généré.

Les citations sont fabriquées à partir des champs de l'article (auteur, titre, numéro, date, adresse) ;
aucune pagination n'étant tenue par la revue, elles n'en comportent pas.
"""
import io
import re
import unicodedata
from html import escape as html_escape
from html.parser import HTMLParser
from xml.sax.saxutils import escape

from markupsafe import Markup

JOURNAL = "Massey Law Review"
# Palette des PDF = celle de l'en-tête du site (bleu nuit + or)
NAVY_HEX, NAVY2_HEX, NAVY_DEEP_HEX = "#0B1E36", "#122D4F", "#081729"
GOLD_BRIGHT_HEX = "#D9B063"   # or vif (sur fond sombre)
GOLD_HEX = "#A8782A"          # or foncé (texte sur fond clair)
RULE_HEX = "#D5DBE4"
_HONORIFICS = re.compile(r"^\s*(?:Me|Mᵉ|Maître|Pr|Prof|Professeur|Professeure|Dr|Docteur)\.?\s+", re.I)
_ORG_WORDS = ("équipe", "equipe", "comité", "comite", "rédaction", "redaction", "review", "revue", "team", "editorial")


def parse_issue(label):
    """« Vol. 1, n° 2 » -> (1, 2). Valeurs absentes = None."""
    label = label or ""
    v = re.search(r"vol(?:ume)?\.?\s*(\d+)", label, re.I)
    n = re.search(r"(?:n[°o]|no\.?|num[ée]ro|issue)\s*\.?\s*(\d+)", label, re.I)
    return (int(v.group(1)) if v else None, int(n.group(1)) if n else None)


def year_of(article):
    d = (article["published_at"] or article["created_at"] or "")[:4]
    return d if d.isdigit() else ""


def clean_author(name):
    """Nom d'auteur sans titre honorifique ni mention de la revue (« Équipe éditoriale — Massey Law Review »)."""
    name = _HONORIFICS.sub("", (name or "").strip())
    name = re.split(r"\s+[—–-]\s+", name)[0].strip()
    return name or "Rédaction"


def _is_org(name):
    low = name.lower()
    return any(w in low for w in _ORG_WORDS) or " " not in name.strip()


def _apa_name(name):
    if _is_org(name):
        return name
    parts = name.split()
    last = parts[-1]
    initials = " ".join(p[0].upper() + "." for p in parts[:-1] if p and p[0].isalpha())
    return "%s, %s" % (last, initials) if initials else last


def _chicago_name(name):
    """Chicago (bibliographie) : « Nom, Prénom » pour une personne ; les organisations restent telles quelles."""
    if _is_org(name):
        return name
    parts = name.split()
    return "%s, %s" % (parts[-1], " ".join(parts[:-1]))


def _chicago_vn(vol, num):
    out = ""
    if vol:
        out += " %d" % vol
    if num:
        out += (", " if vol else " ") + "no. %d" % num
    return out


def citations(article, url):
    """Dictionnaire {style: texte} : chicago, bluebook, apa, bibtex, ris."""
    author = clean_author(article["author_name"])
    title = (article["title"] or "").strip()
    year = year_of(article)
    vol, num = parse_issue(article["issue_label"])
    vn_fr = ", ".join(x for x in ("vol. %d" % vol if vol else "", "n° %d" % num if num else "") if x)
    chicago = "%s. “%s.” %s%s%s. %s." % (_chicago_name(author), title.rstrip(".?!") if not title.endswith(("?", "!")) else title[:-1],
                                          JOURNAL, _chicago_vn(vol, num), (" (%s)" % year) if year else "", url)
    if title.endswith(("?", "!")):
        chicago = chicago.replace("%s.”" % title[:-1], "%s%s”" % (title[:-1], title[-1]))
    blue = "%s, %s, %s%s%s, %s." % (author, title, ("%d " % vol) if vol else "", "MASSEY L. REV.",
                                    (" (%s)" % year) if year else "", url)
    apa = "%s %s %s. %s%s %s" % (
        _apa_name(author) + ("" if _apa_name(author).endswith(".") else "."),
        ("(%s)." % year) if year else "(s. d.).", title.rstrip("."),
        JOURNAL + (", %d" % vol if vol else ""), ("(%d)." % num) if num else ".", url)
    apa = re.sub(r"\s+", " ", apa).replace(" .", ".")
    key = re.sub(r"[^a-z0-9]", "", (author.split()[-1] if author else "massey").lower()) + year + (article["slug"][:8].replace("-", "") if article["slug"] else "")
    bib = ["@article{%s," % key,
           "  author  = {%s}," % author, "  title   = {%s}," % title, "  journal = {%s}," % JOURNAL]
    if vol:
        bib.append("  volume  = {%d}," % vol)
    if num:
        bib.append("  number  = {%d}," % num)
    if year:
        bib.append("  year    = {%s}," % year)
    bib += ["  url     = {%s}" % url, "}"]
    ris = ["TY  - JOUR", "AU  - %s" % author, "TI  - %s" % title, "JO  - %s" % JOURNAL]
    if vol:
        ris.append("VL  - %d" % vol)
    if num:
        ris.append("IS  - %d" % num)
    if year:
        ris.append("PY  - %s" % year)
    ris += ["UR  - %s" % url, "ER  - ", ""]
    return {"chicago": chicago, "bluebook": blue, "apa": apa, "bibtex": "\n".join(bib), "ris": "\r\n".join(ris)}


CITE_STYLES = [("chicago", "Chicago"), ("bluebook", "Bluebook"), ("apa", "APA 7"), ("bibtex", "BibTeX"), ("ris", "RIS (Zotero, EndNote)")]


# ---------------------------------------------------------------- PDF
class _Blocks(HTMLParser):
    """HTML d'article -> liste de blocs (type, texte avec balises <b>/<i> reportlab)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.cur, self.kind, self.list_depth = [], [], None, 0

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        if text and self.kind:
            self.blocks.append((self.kind, text))
        self.cur, self.kind = [], None

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "p", "li", "blockquote"):
            self._flush()
            self.kind = {"h1": "h2", "h4": "h3", "li": "li"}.get(tag, tag)
        elif tag in ("strong", "b"):
            self.cur.append("<b>")
        elif tag in ("em", "i"):
            self.cur.append("<i>")
        elif tag == "br":
            self.cur.append(" ")

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "p", "li", "blockquote"):
            self._flush()
        elif tag in ("strong", "b"):
            self.cur.append("</b>")
        elif tag in ("em", "i"):
            self.cur.append("</i>")

    def handle_data(self, data):
        if self.kind is None and data.strip():
            self.kind = "p"
        if self.kind:
            self.cur.append(escape(data))

    def close(self):
        super().close()
        self._flush()


# ---------------------------------------------------------------- présentation
_MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def fr_date(value):
    """« 2026-10-19 10:00:00 » -> « 19 oct. 2026 »."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not m:
        return value or ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return "%s %s %d" % ("1er" if d == 1 else d, _MONTHS[mo - 1] if 1 <= mo <= 12 else "", y)


def reading_minutes(body_html):
    words = len(re.sub(r"<[^>]+>", " ", body_html or "").split())
    return max(1, round(words / 220)) if words else 0


def issue_slug(label):
    base = unicodedata.normalize("NFKD", (label or "").lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")


def _fold(text):
    """Texte replié (minuscules, sans accents) de MÊME longueur que l'original, pour repérer des mots sans tenir compte des accents."""
    out = []
    for ch in text:
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).lower()
        out.append(base[0] if base else ch.lower())
    return "".join(out)


def search_terms(q):
    return [t for t in _fold(q or "").split() if len(t) > 1]


def mark(text, terms):
    """HTML sûr : le texte échappé, avec les mots recherchés entourés de <mark> (insensible aux accents et à la casse)."""
    text = text or ""
    if not terms:
        return Markup(html_escape(text))
    folded = _fold(text)
    spans = []
    for t in terms:
        for m in re.finditer(re.escape(t), folded):
            spans.append((m.start(), m.end()))
    if not spans:
        return Markup(html_escape(text))
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, pos = [], 0
    for a, b in merged:
        out.append(html_escape(text[pos:a]))
        out.append("<mark>%s</mark>" % html_escape(text[a:b]))
        pos = b
    out.append(html_escape(text[pos:]))
    return Markup("".join(out))


def body_snippet(body_html, terms, width=230):
    """Extrait du texte intégral autour du premier mot trouvé (chaîne vide s'il n'y en a pas)."""
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body_html or "")).strip()
    folded = _fold(plain)
    hits = [folded.find(t) for t in terms if folded.find(t) >= 0]
    if not hits:
        return ""
    p = min(hits)
    a, b = max(0, p - width // 3), min(len(plain), p + width)
    if a:
        a = plain.rfind(" ", 0, a + 1) + 1 or a
    if b < len(plain):
        b = plain.find(" ", b) if plain.find(" ", b) > 0 else len(plain)
    return ("… " if a else "") + plain[a:b].strip() + (" …" if b < len(plain) else "")


def citations_html(article, url):
    """Versions mises en forme (italiques) des citations chicago / bluebook / apa, pour un collage fidèle dans Word."""
    esc = html_escape
    author = clean_author(article["author_name"])
    title = (article["title"] or "").strip()
    year = year_of(article)
    vol, num = parse_issue(article["issue_label"])
    vn_fr = ", ".join(x for x in ("vol. %d" % vol if vol else "", "n° %d" % num if num else "") if x)
    t_end = title[-1] if title.endswith(("?", "!")) else "."
    t_core = title[:-1] if title.endswith(("?", "!")) else title.rstrip(".")
    chicago = "%s. “%s%s” <i>%s</i>%s%s. %s." % (esc(_chicago_name(author)), esc(t_core), t_end, JOURNAL,
                                                esc(_chicago_vn(vol, num)), esc((" (%s)" % year) if year else ""), esc(url))
    blue = "%s, <i>%s</i>, %sMASSEY L. REV.%s, %s." % (esc(author), esc(title), ("%d " % vol) if vol else "",
                                                       esc((" (%s)" % year) if year else ""), esc(url))
    name = _apa_name(author)
    name = name if name.endswith(".") else name + "."
    journal = "<i>%s%s</i>%s." % (JOURNAL, esc(", %d" % vol) if vol else "", esc("(%d)" % num) if num else "")
    apa = "%s %s %s %s %s" % (esc(name), esc("(%s)." % year if year else "(s. d.)."), esc(title.rstrip(".") + "."), journal, esc(url))
    return {"chicago": chicago, "bluebook": blue, "apa": re.sub(r"\s+", " ", apa).replace(" .", ".")}

# ---------------------------------------------------------------- Notes de bas de page
_FN_REF = re.compile(r"\[\^(\d{1,3})\]")


def parse_footnotes(raw):
    """Texte saisi (une note par ligne, numérotation automatique) -> liste de notes ; « 1. », « 1) », « [1] » en tête sont ignorés."""
    notes = []
    for line in (raw or "").replace("\r", "").split("\n"):
        line = re.sub(r"^\s*(?:\[\^?\d+\]|\d+\s*[.)])\s*", "", line).strip()
        if line:
            notes.append(line)
    return notes


def _note_html(text):
    """Note -> HTML sûr : tout est échappé, sauf *italique* et les adresses web (liens)."""
    out = html_escape(text, quote=False)
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", out)
    out = re.sub(r"(https?://[^\s<]+[^\s<.,;:)])", r'<a href="\1" rel="noopener" target="_blank">\1</a>', out)
    return out


def render_bibliography(raw):
    """Bibliographie saisie (une référence par ligne) -> section HTML à intertitre (reprise dans le sommaire) ; vide si rien."""
    refs = parse_footnotes(raw)
    if not refs:
        return ""
    return ('<section class="rva-notes rva-biblio" id="bibliographie" data-toc="Bibliographie" aria-label="Bibliographie">'
            '<details id="rva-biblio-d"><summary><span class="rva-notes-t">Bibliographie</span>'
            '<span class="rva-notes-s"><span class="rva-on-closed">Afficher</span><span class="rva-on-open">Masquer</span>'
            '<i class="rva-pm" aria-hidden="true"></i></span></summary><ul>%s</ul></details></section>'
            % "".join("<li>%s</li>" % _note_html(r) for r in refs))


def render_footnotes(body_html, raw_notes):
    """Remplace [^n] dans le corps par des appels de note et construit la section « Notes de bas de page ».
    Retourne (html_du_corps_avec_appels, html_de_la_section_notes_ou_vide)."""
    notes = parse_footnotes(raw_notes)
    body = body_html or ""
    if not notes:
        return body, ""

    def ref(m):
        n = int(m.group(1))
        if not 1 <= n <= len(notes):
            return m.group(0)
        plain = re.sub(r"\*", "", notes[n - 1])
        return ('<sup class="rva-fnref"><a href="#fn-%d" id="fnref-%d" role="doc-noteref" data-note="%s" aria-label="Note %d">%d</a></sup>'
                % (n, n, html_escape(plain, quote=True)[:600], n, n))

    body = _FN_REF.sub(ref, body)
    items = "".join('<li id="fn-%d" role="doc-endnote"><span class="rva-fnnum">%d</span><span class="rva-fntext">%s '
                    '<a class="rva-fnback" href="#fnref-%d" role="doc-backlink" aria-label="Retour au texte (appel %d)">↩</a></span></li>'
                    % (i, i, _note_html(t), i, i) for i, t in enumerate(notes, 1))
    section = ('<section class="rva-notes" role="doc-endnotes" aria-label="Notes de bas de page">'
               '<details id="rva-notes-d"><summary><span class="rva-notes-t">Notes de bas de page</span>'
               '<span class="rva-notes-s"><span class="rva-on-closed">Afficher</span><span class="rva-on-open">Masquer</span>'
               '<i class="rva-pm" aria-hidden="true"></i></span></summary><ol>%s</ol></details></section>' % items)
    return body, section


DOMAINS = [("contrats", "Contrats"), ("fiscalite", "Fiscalité"), ("arbitrage", "Arbitrage et différends"),
           ("commerce_international", "Commerce international"), ("societes", "Sociétés et investissement"),
           ("travail", "Travail"), ("procedure", "Procédure"), ("legaltech", "Droit et technologie"), ("autre", "Autres sujets")]
ARTICLE_TYPES = [("etude", "Étude"), ("note", "Note pratique"), ("commentaire", "Commentaire"),
                 ("etude_cas", "Étude de cas"), ("chronique", "Chronique")]
DOMAIN_LABELS = dict(DOMAINS)
TYPE_LABELS = dict(ARTICLE_TYPES)


def _styles():
    from reportlab.lib.styles import ParagraphStyle
    import ci_pdf
    from reportlab.lib import colors as _c
    burg, ink, soft, acc = _c.HexColor(NAVY_HEX), _c.HexColor("#1B2430"), _c.HexColor("#4A5568"), _c.HexColor(GOLD_HEX)
    return {
        "kicker": ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=8, textColor=acc, spaceAfter=6, leading=11),
        "title": ParagraphStyle("t", fontName="Times-Bold", fontSize=24, leading=28, textColor=burg, spaceAfter=8),
        "author": ParagraphStyle("au", fontName="Times-Italic", fontSize=13, leading=17, textColor=acc, spaceAfter=3),
        "abslabel": ParagraphStyle("al", fontName="Helvetica-Bold", fontSize=7.4, leading=10, textColor=acc, spaceAfter=3),
        "note": ParagraphStyle("nt", fontName="Times-Roman", fontSize=9.2, leading=12.6, textColor=soft, leftIndent=14, firstLineIndent=-14, spaceAfter=3),
        "fnote": ParagraphStyle("fnt", fontName="Times-Roman", fontSize=8.6, leading=11.2, textColor=soft, leftIndent=12, firstLineIndent=-12),
        "fine": ParagraphStyle("fn", fontName="Helvetica-Oblique", fontSize=7.6, leading=10.5, textColor=soft),
        "meta": ParagraphStyle("m", fontName="Helvetica", fontSize=9.5, leading=13, textColor=soft, spaceAfter=2),
        "abs": ParagraphStyle("a", fontName="Times-Italic", fontSize=11, leading=15.5, textColor=ink, spaceAfter=2),
        "h2": ParagraphStyle("h2", fontName="Times-Bold", fontSize=14.5, leading=18, textColor=burg, spaceBefore=14, spaceAfter=5, keepWithNext=1),
        "h3": ParagraphStyle("h3", fontName="Times-Bold", fontSize=12, leading=16, textColor=ink, spaceBefore=10, spaceAfter=4, keepWithNext=1),
        "p": ParagraphStyle("p", fontName="Times-Roman", fontSize=11, leading=16, textColor=ink, spaceAfter=7, alignment=4),
        "li": ParagraphStyle("li", fontName="Times-Roman", fontSize=11, leading=15.5, textColor=ink, leftIndent=14, bulletIndent=3, spaceAfter=3),
        "blockquote": ParagraphStyle("bq", fontName="Times-Italic", fontSize=10.5, leading=15, textColor=soft, leftIndent=18, rightIndent=10, spaceAfter=7),
        "cite": ParagraphStyle("c", fontName="Helvetica", fontSize=8.8, leading=12.5, textColor=soft),
        "cover": ParagraphStyle("cv", fontName="Times-Bold", fontSize=34, leading=40, textColor=ink, spaceAfter=10),
        "toc": ParagraphStyle("toc", fontName="Times-Roman", fontSize=12, leading=17, textColor=ink, spaceAfter=7, leftIndent=12),
    }


GOLD = GOLD_HEX
_LOGOS = {}


def _logo(kind):
    """Logo de la marque (static/img/mark-header.png, crème sur transparent) en 'cream' ou recoloré 'burgundy'."""
    if kind in _LOGOS:
        return _LOGOS[kind]
    import os
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "mark-header.png")
    try:
        im = Image.open(path).convert("RGBA")
        if kind == "accent":
            solid = Image.new("RGBA", im.size, (11, 30, 54, 255))
            solid.putalpha(im.getchannel("A"))
            im = solid
        _LOGOS[kind] = (ImageReader(im), im.size)
    except Exception:
        _LOGOS[kind] = None
    return _LOGOS[kind]


def _draw_logo(canvas, kind, x, y, h):
    lg = _logo(kind)
    if not lg:
        return 0
    reader, (w0, h0) = lg
    w = h * w0 / h0
    canvas.drawImage(reader, x, y, width=w, height=h, mask="auto")
    return w


def issue_ref(article):
    vol, num = parse_issue(article["issue_label"])
    return "MLR-%s-%s%s-%03d" % (year_of(article) or "0000", ("V%d" % vol) if vol else "", ("N%d" % num) if num else "", article["id"] or 0)


def _flow(article, url, cite_text, st, masthead=True, fn=None, idx=0):
    """Éléments reportlab d'un article : en-tête éditorial, résumé encadré, texte, bloc « Pour citer »."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, Spacer, Table, TableStyle
    import ci_pdf

    c = ci_pdf._c
    keys = article.keys()
    dom = DOMAIN_LABELS.get(article["domain"]) if "domain" in keys else None
    typ = TYPE_LABELS.get(article["article_type"]) if "article_type" in keys else None
    kicker = "  ·  ".join(x for x in (typ, dom) if x) or (article["issue_label"] or JOURNAL)
    mins = reading_minutes(article["body_html"])
    story = [Paragraph(c(kicker.upper()), st["kicker"]),
             Paragraph(c(article["title"]), st["title"]),
             *([Paragraph(c(clean_author(article["author_name"])), st["author"])]
               if clean_author(article["author_name"]).strip().lower() not in ("rédaction", "redaction") else []),
             Paragraph(c(" · ".join(x for x in (fr_date(article["published_at"] or article["created_at"]),
                                                article["issue_label"] or "", ("%d min de lecture" % mins) if mins else "") if x)), st["meta"]),
             Spacer(1, 6), HRFlowable(width="100%", thickness=0.9, color=colors.HexColor(NAVY_HEX), spaceAfter=1.5),
             HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(GOLD_BRIGHT_HEX), spaceAfter=11)]
    if article["abstract"]:
        box = Table([[Paragraph("RÉSUMÉ", st["abslabel"])], [Paragraph(c(article["abstract"]), st["abs"])]], colWidths=["100%"])
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F2F5F9")),
                                 ("LINEBEFORE", (0, 0), (0, -1), 2.4, colors.HexColor(GOLD_BRIGHT_HEX)),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                                 ("TOPPADDING", (0, 0), (0, 0), 9), ("BOTTOMPADDING", (0, -1), (-1, -1), 10)]))
        story += [box, Spacer(1, 12)]
    parser = _Blocks()
    parser.feed(article["body_html"] or "")
    parser.close()
    notes = parse_footnotes(article["footnotes"] if "footnotes" in keys else "")
    cited = set()
    first_p = True
    for kind, text in parser.blocks:
        marked = text
        for k, v in ci_pdf._REPL.items():
            marked = marked.replace(k, v)
        marked = marked.encode("cp1252", "replace").decode("cp1252")
        marked = re.sub(r"<(?!/?[bi]>)", "&lt;", marked)
        def _mark(m):
            n = int(m.group(1))
            if not 1 <= n <= len(notes):
                return m.group(0)
            cited.add(n)
            key = "%d:%d" % (idx, n)
            if fn is not None:
                fn.notes[key] = (n, _pdf_note_html(notes[n - 1]))
            return ('<super><font color="#A8782A" size="7"><b>%d</b></font></super>' % n
                    + ('<onDraw name="fnmark" label="%s"/>' % key if fn is not None else ""))
        marked = _FN_REF.sub(_mark, marked)
        if first_p and kind == "p":
            first_p = False
            m0 = re.match(r"^((?:[^<\s]+\s+){0,3}[^<\s]+)(\s.*)$", marked, re.S)
            if m0 and not re.search(r"[<&\[]", m0.group(1)):
                marked = '<font color="%s" size="9.8"><b>%s</b></font>%s' % (NAVY_HEX, m0.group(1).upper(), m0.group(2))
        try:
            story.append(Paragraph(marked, st[kind], bulletText="•" if kind == "li" else None))
        except Exception:
            story.append(Paragraph(c(re.sub(r"</?[bi]>", "", text)), st[kind], bulletText="-" if kind == "li" else None))
    biblio = parse_footnotes(article["bibliography"] if "bibliography" in keys else "")
    if biblio:
        story += [Spacer(1, 14), Paragraph("BIBLIOGRAPHIE", st["abslabel"]),
                  HRFlowable(width="100%", thickness=0.6, color=colors.HexColor(NAVY_HEX), spaceAfter=6)]
        for t in biblio:
            txt = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", c(t))
            story.append(Paragraph(txt, st["note"]))
    # sans pagination dynamique (fn=None) toutes les notes vont en fin de texte ; sinon seules les notes jamais appelées y figurent
    rest = [(i, t) for i, t in enumerate(notes, 1) if fn is None or i not in cited]
    if rest:
        story += [Spacer(1, 14), Paragraph("NOTES" if fn is not None else "NOTES DE BAS DE PAGE", st["abslabel"]),
                  HRFlowable(width="100%", thickness=0.6, color=colors.HexColor(NAVY_HEX), spaceAfter=6)]
        for i, t in rest:
            story.append(Paragraph('<font color="#A8782A"><b>%d</b></font>&nbsp;&nbsp;%s' % (i, _pdf_note_html(t)), st["note"]))
    cite_par = c(cite_text).replace(JOURNAL, "<i>%s</i>" % JOURNAL)
    cite = Table([[Paragraph("POUR CITER CET ARTICLE", st["abslabel"])], [Paragraph(cite_par, st["cite"])],
                  [Paragraph(c("Référence : %s  ·  %s" % (issue_ref(article), url)), st["cite"])],
                  [Paragraph(c("Les opinions exprimées engagent leurs auteurs et ne constituent pas un avis juridique. "
                               "© %s %s. Tous droits réservés." % (year_of(article), JOURNAL)), st["fine"])]], colWidths=["100%"])
    cite.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(RULE_HEX)),
                              ("LINEABOVE", (0, 0), (-1, 0), 2.2, colors.HexColor(NAVY_HEX)),
                              ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, -1), (-1, -1), 9)]))
    story += [Spacer(1, 18), KeepTogether([cite])]
    return story


def _raw(text):
    """Texte pour canvas.drawString : jeu WinAnsi, sans échappement XML."""
    import ci_pdf
    s = str(text or "")
    for k, v in ci_pdf._REPL.items():
        s = s.replace(k, v)
    return s.encode("cp1252", "replace").decode("cp1252")


def colors_navy():
    from reportlab.lib import colors
    return colors.HexColor(NAVY_HEX)


def _decorate(label_right, cover_label=None):
    """Fonctions de dessin des pages : bandeau de marque (1re page), en-tête courant, pied de page, couverture."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    import ci_pdf
    W, H = A4
    burg, gold, soft = colors.HexColor(NAVY_HEX), colors.HexColor(GOLD_BRIGHT_HEX), colors.HexColor("#4A5568")

    def grad(canvas, x, y, w, h, c1, c2, horizontal=True, steps=60):
        a, b = colors.HexColor(c1), colors.HexColor(c2)
        for i in range(steps):
            t = i / float(steps - 1)
            canvas.setFillColorRGB(a.red + (b.red - a.red) * t, a.green + (b.green - a.green) * t, a.blue + (b.blue - a.blue) * t)
            if horizontal:
                canvas.rect(x + w * i / steps, y, w / steps + 0.6, h, stroke=0, fill=1)
            else:
                canvas.rect(x, y + h * i / steps, w, h / steps + 0.6, stroke=0, fill=1)

    def footer(canvas, doc):
        canvas.setStrokeColor(colors.HexColor(RULE_HEX)); canvas.setLineWidth(0.5)
        canvas.line(20 * mm, 16 * mm, W - 20 * mm, 16 * mm)
        canvas.setFont("Helvetica", 7.8); canvas.setFillColor(soft)
        canvas.drawString(20 * mm, 11.5 * mm, _raw(JOURNAL + "  ·  Massey Contracts & Tax"))
        canvas.drawRightString(W - 20 * mm, 11.5 * mm, "%d" % doc.page)

    def first(canvas, doc):
        canvas.saveState()
        grad(canvas, 0, H - 40 * mm, W, 40 * mm, NAVY_DEEP_HEX, NAVY2_HEX)
        grad(canvas, 0, H - 41.2 * mm, W, 1.2 * mm, "#B8893A", "#F0D79A", steps=40)
        w = _draw_logo(canvas, "cream", 20 * mm, H - 32 * mm, 22 * mm)
        x = 20 * mm + w + 6 * mm
        canvas.setFillColor(colors.white); canvas.setFont("Times-Bold", 21)
        canvas.drawString(x, H - 21.5 * mm, "MASSEY LAW REVIEW")
        canvas.setFont("Helvetica", 7.8); canvas.setFillColor(colors.HexColor("#E9D9B8"))
        canvas.drawString(x, H - 27.5 * mm, "REVUE JURIDIQUE  ·  MASSEY CONTRACTS & TAX")
        canvas.setFont("Helvetica-Bold", 9); canvas.setFillColor(colors.white)
        canvas.drawRightString(W - 20 * mm, H - 21.5 * mm, _raw(label_right))
        canvas.setFont("Helvetica", 7.8); canvas.setFillColor(colors.HexColor("#E9D9B8"))
        canvas.drawRightString(W - 20 * mm, H - 27.5 * mm, "Droit des contrats · Fiscalité · Transactions")
        footer(canvas, doc)
        canvas.restoreState()

    def later(canvas, doc):
        canvas.saveState()
        w = _draw_logo(canvas, "accent", 20 * mm, H - 16.5 * mm, 8 * mm)
        canvas.setFillColor(burg); canvas.setFont("Times-Bold", 10.5)
        canvas.drawString(20 * mm + w + 3 * mm, H - 13.4 * mm, "MASSEY LAW REVIEW")
        canvas.setFont("Helvetica", 7.8); canvas.setFillColor(soft)
        canvas.drawRightString(W - 20 * mm, H - 13.4 * mm, _raw(label_right)[:80])
        canvas.setStrokeColor(burg); canvas.setLineWidth(0.9); canvas.line(20 * mm, H - 19 * mm, W - 20 * mm, H - 19 * mm)
        canvas.setStrokeColor(gold); canvas.setLineWidth(0.4); canvas.line(20 * mm, H - 20 * mm, W - 20 * mm, H - 20 * mm)
        footer(canvas, doc)
        canvas.restoreState()

    def cover(canvas, doc):
        canvas.saveState()
        grad(canvas, 0, 0, W, H, NAVY2_HEX, NAVY_DEEP_HEX, horizontal=False, steps=80)
        canvas.setFillColor(colors.HexColor(NAVY_DEEP_HEX)); canvas.rect(0, 0, W, 62 * mm, stroke=0, fill=1)
        canvas.setFillColor(gold); canvas.rect(20 * mm, 62 * mm, 40 * mm, 1.2 * mm, stroke=0, fill=1)
        lh = 46 * mm
        _draw_logo(canvas, "cream", 20 * mm, H - 36 * mm - lh, lh)
        canvas.setFillColor(colors.white); canvas.setFont("Times-Bold", 40)
        canvas.drawString(20 * mm, H / 2 - 4 * mm, "MASSEY")
        canvas.drawString(20 * mm, H / 2 - 20 * mm, "LAW REVIEW")
        canvas.setFont("Helvetica", 9); canvas.setFillColor(colors.HexColor("#E9D9B8"))
        canvas.drawString(20 * mm, H / 2 - 30 * mm, "REVUE JURIDIQUE  ·  MASSEY CONTRACTS & TAX")
        canvas.setFont("Times-Roman", 11); canvas.setFillColor(colors.HexColor("#F3E9D8"))
        canvas.drawString(20 * mm, 42 * mm, _raw(cover_label or ""))
        canvas.setFont("Helvetica", 8); canvas.setFillColor(colors.HexColor("#E9D9B8"))
        canvas.drawString(20 * mm, 24 * mm, "Droit des contrats  ·  Fiscalité des affaires  ·  Transactions transfrontalières")
        canvas.restoreState()

    return first, later, cover


def _pdf_note_html(t):
    import ci_pdf
    return re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", ci_pdf._c(t))


class _FNCtx:
    """Notes de bas de page des PDF : chaque note est dessinée au bas de la page où son numéro est cité.
    La place nécessaire est réservée par mise en page itérative (le texte reflue autour des notes)."""

    def __init__(self):
        self.notes = {}      # clé "article:n" -> (n, html)
        self.pages = {}      # page -> [clés dans l'ordre d'apparition] (passe en cours)
        self.reserve = {}    # page -> hauteur réservée (points)
        self.doc = None
        self.width = 0

    def mark(self, canvas, kind, label):
        page = self.doc.page if self.doc else 0
        lst = self.pages.setdefault(page, [])
        if label in self.notes and label not in lst and not any(label in v for v in self.pages.values()):
            lst.append(label)

    def _paras(self, st, keys):
        from reportlab.platypus import Paragraph
        out = []
        for k in keys:
            n, html = self.notes[k]
            out.append(Paragraph('<font color="#A8782A"><b>%d</b></font>&nbsp;&nbsp;%s' % (n, html), st["fnote"]))
        return out

    def need(self, st, keys):
        from reportlab.lib.units import mm
        h = 0
        for p in self._paras(st, keys):
            h += p.wrap(self.width, 10000)[1] + 2
        return h + 5 * mm

    def padding(self):
        from reportlab.lib.units import mm
        r = self.reserve.get(getattr(self.doc, "page", 0) if self.doc else 0, 0)
        return r + 3 * mm if r else 0

    def draw(self, canvas, doc, st):
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        keys = self.pages.get(doc.page)
        if not keys:
            return
        paras = self._paras(st, keys)
        hs = [p.wrap(self.width, 10000)[1] for p in paras]
        y = 20 * mm + sum(hs) + 2 * len(hs)
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor(GOLD_BRIGHT_HEX)); canvas.setLineWidth(0.8)
        canvas.line(20 * mm, y + 3 * mm, 20 * mm + 42 * mm, y + 3 * mm)
        for p, h in zip(paras, hs):
            y -= h + 2
            p.drawOn(canvas, 20 * mm, y)
        canvas.restoreState()


def _build(story_fn, title, author, label_right, mode="article", cover_label=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate
    import ci_pdf

    W, H = A4
    first, later, cover = _decorate(label_right, cover_label)
    st = _styles()
    ctx = _FNCtx()
    ctx.width = W - 40 * mm

    class _Frame(Frame):
        def _reset(self):
            self.__dict__["_bottomPadding"] = ctx.padding() if ctx.doc else 0
            self._geom()
            Frame._reset(self)

    class _Doc(BaseDocTemplate):
        def handle_pageBegin(self):
            BaseDocTemplate.handle_pageBegin(self)
            self.canv.setNamedCB("fnmark", ctx.mark)

    def one_pass():
        buf = io.BytesIO()
        doc = _Doc(buf, pagesize=A4, title=title, author=author, subject=JOURNAL, creator=JOURNAL)
        ctx.doc = doc
        ctx.pages = {}
        fr = lambda top: _Frame(20 * mm, 20 * mm, W - 40 * mm, H - 20 * mm - top, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        end = lambda canv, d: ctx.draw(canv, d, st)
        templates = [PageTemplate(id="later", frames=[fr(26 * mm)], onPage=later, onPageEnd=end)]
        if mode == "issue":
            templates.insert(0, PageTemplate(id="cover", frames=[fr(H - 30 * mm)], onPage=cover, onPageEnd=end))
        else:
            templates.insert(0, PageTemplate(id="first", frames=[fr(50 * mm)], onPage=first, onPageEnd=end, autoNextPageTemplate="later"))
        doc.addPageTemplates(templates)
        doc.build(story_fn(ctx))
        return buf.getvalue()

    data = one_pass()
    seen = []
    for _ in range(7):
        need = {p: ctx.need(st, ks) for p, ks in ctx.pages.items() if ks}
        if all(need.get(p, 0) <= ctx.reserve.get(p, 0) + 0.5 for p in need) and all(ctx.reserve.get(p, 0) <= need.get(p, 0) + 0.5 for p in ctx.reserve):
            break
        seen.append(dict(ctx.reserve))
        if need in seen or len(seen) > 4:  # oscillation : on réserve le maximum connu pour garantir de la place
            for p in set(need) | set(ctx.reserve):
                ctx.reserve[p] = max(need.get(p, 0), ctx.reserve.get(p, 0))
        else:
            ctx.reserve = need
        data = one_pass()
    return data


def article_pdf(article, url, cite_text):
    """PDF A4 de l'article : bandeau de marque, en-tête courant, résumé encadré, bloc « Pour citer » (reportlab, polices standard)."""
    right = " · ".join(x for x in (article["issue_label"], year_of(article)) if x) or JOURNAL
    return _build(lambda fn: _flow(article, url, cite_text, _styles(), fn=fn), article["title"], clean_author(article["author_name"]), right)


def issue_pdf(label, articles, url_of, cite_of):
    """PDF d'un numéro complet : couverture de marque, sommaire, puis chaque article sur de nouvelles pages."""
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, NextPageTemplate, PageBreak, Paragraph, Spacer
    import ci_pdf

    c = ci_pdf._c
    st = _styles()
    newest = max((a["published_at"] or a["created_at"] or "") for a in articles)
    cover_label = "%s  ·  %d article%s  ·  paru le %s" % (label, len(articles), "s" if len(articles) > 1 else "", fr_date(newest))
    def make(fn):
        fn.notes.clear()
        story = [NextPageTemplate("later"), Spacer(1, 1), PageBreak(), Paragraph(c("Sommaire"), st["h2"]),
                 HRFlowable(width="100%", thickness=0.5, color=colors_navy(), spaceAfter=10)]
        for n, a in enumerate(articles, 1):
            story.append(Paragraph("<font color='#A8782A'>%02d</font>&nbsp;&nbsp;<b>%s</b><br/>%s" % (n, c(a["title"]), c(clean_author(a["author_name"]))), st["toc"]))
        for k, a in enumerate(articles):
            story.append(PageBreak())
            story += _flow(a, url_of(a), cite_of(a), st, masthead=False, fn=fn, idx=k)
        return story

    return _build(make, "%s — %s" % (JOURNAL, label), JOURNAL, label, mode="issue", cover_label=cover_label)
