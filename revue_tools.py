"""Massey Law Review : citations, métadonnées d'articles et PDF généré.

Les citations sont fabriquées à partir des champs de l'article (auteur, titre, numéro, date, adresse) ;
aucune pagination n'étant tenue par la revue, elles n'en comportent pas.
"""
import io
import re
from html.parser import HTMLParser
from xml.sax.saxutils import escape

JOURNAL = "Massey Law Review"
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


def citations(article, url):
    """Dictionnaire {style: texte} : massey, bluebook, apa, bibtex, ris."""
    author = clean_author(article["author_name"])
    title = (article["title"] or "").strip()
    year = year_of(article)
    vol, num = parse_issue(article["issue_label"])
    vn_fr = ", ".join(x for x in ("vol. %d" % vol if vol else "", "n° %d" % num if num else "") if x)
    massey = "%s, « %s », %s%s%s, %s." % (author, title, JOURNAL, (", " + vn_fr) if vn_fr else "",
                                           (", " + year) if year else "", url)
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
    return {"massey": massey, "bluebook": blue, "apa": apa, "bibtex": "\n".join(bib), "ris": "\r\n".join(ris)}


CITE_STYLES = [("massey", "Massey (français)"), ("bluebook", "Bluebook"), ("apa", "APA 7"), ("bibtex", "BibTeX"), ("ris", "RIS (Zotero, EndNote)")]


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


def article_pdf(article, url, cite_text):
    """PDF A4 de l'article (reportlab, polices standard) : titre, auteur, résumé, texte, citation, pied de page."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
    import ci_pdf

    c = ci_pdf._c
    burg, ink, soft = ci_pdf.BURGUNDY, ci_pdf.INK, ci_pdf.SOFT
    st = {
        "kicker": ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=8.5, textColor=burg, spaceAfter=6, leading=11),
        "title": ParagraphStyle("t", fontName="Times-Bold", fontSize=22, leading=26, textColor=ink, spaceAfter=8),
        "meta": ParagraphStyle("m", fontName="Helvetica", fontSize=9.5, leading=13, textColor=soft, spaceAfter=2),
        "abs": ParagraphStyle("a", fontName="Times-Italic", fontSize=11, leading=15.5, textColor=ink, spaceBefore=8, spaceAfter=10,
                              leftIndent=8, borderPadding=(0, 0, 0, 8)),
        "h2": ParagraphStyle("h2", fontName="Times-Bold", fontSize=14.5, leading=18, textColor=burg, spaceBefore=14, spaceAfter=5),
        "h3": ParagraphStyle("h3", fontName="Times-Bold", fontSize=12, leading=16, textColor=ink, spaceBefore=10, spaceAfter=4),
        "p": ParagraphStyle("p", fontName="Times-Roman", fontSize=11, leading=16, textColor=ink, spaceAfter=7, alignment=4),
        "li": ParagraphStyle("li", fontName="Times-Roman", fontSize=11, leading=15.5, textColor=ink, leftIndent=14, bulletIndent=3, spaceAfter=3),
        "blockquote": ParagraphStyle("bq", fontName="Times-Italic", fontSize=10.5, leading=15, textColor=soft, leftIndent=18, rightIndent=10, spaceAfter=7),
        "cite": ParagraphStyle("c", fontName="Helvetica", fontSize=8.8, leading=12.5, textColor=soft),
    }
    vol, num = parse_issue(article["issue_label"])
    story = [Paragraph(c((article["issue_label"] or JOURNAL).upper()), st["kicker"]),
             Paragraph(c(article["title"]), st["title"]),
             Paragraph(c(article["author_name"]), st["meta"]),
             Paragraph(c("Publié le " + (article["published_at"] or article["created_at"] or "")[:10]), st["meta"]),
             Spacer(1, 4), HRFlowable(width="100%", thickness=0.8, color=burg, spaceAfter=6)]
    if article["abstract"]:
        story.append(Paragraph(c(article["abstract"]), st["abs"]))
    parser = _Blocks()
    parser.feed(article["body_html"] or "")
    parser.close()
    for kind, text in parser.blocks:
        safe = ci_pdf._c(re.sub(r"</?[bi]>", "", text))  # texte sûr (WinAnsi)
        # ré-applique gras/italique sur le texte assaini
        marked = text
        for k, v in ci_pdf._REPL.items():
            marked = marked.replace(k, v)
        marked = marked.encode("latin-1", "replace").decode("latin-1")
        marked = re.sub(r"<(?!/?[bi]>)", "&lt;", marked)
        try:
            story.append(Paragraph(marked, st[kind], bulletText="•" if kind == "li" else None))
        except Exception:
            story.append(Paragraph(safe, st[kind], bulletText="-" if kind == "li" else None))
    story += [Spacer(1, 14), HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#D9D2D0"), spaceAfter=6),
              Paragraph("<b>Pour citer cet article</b>", st["cite"]), Paragraph(c(cite_text), st["cite"]),
              Paragraph(c("Les opinions exprimées engagent leurs auteurs et ne constituent pas un avis juridique."), st["cite"])]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(soft)
        canvas.drawString(20 * mm, 11 * mm, c(JOURNAL + " — " + url)[:110])
        canvas.drawRightString(A4[0] - 20 * mm, 11 * mm, "Page %d" % doc.page)
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
                            title=article["title"], author=clean_author(article["author_name"]), subject=JOURNAL)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
