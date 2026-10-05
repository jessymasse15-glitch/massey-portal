"""Génération de PDF (reportlab) : rapport d'analyse et contrat signé avec certificat.
Les polices standard de reportlab ne couvrent que le jeu Latin-1/WinAnsi : tout
caractère hors de ce jeu est remplacé (jamais d'erreur de génération)."""
import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BURGUNDY = colors.HexColor("#7A1F2B")
INK = colors.HexColor("#211316")
SOFT = colors.HexColor("#5B4A4C")
LINE = colors.HexColor("#D9D2D0")
LEVEL_COLORS = {"eleve": colors.HexColor("#B23A2E"), "moyen": colors.HexColor("#9C6B14"), "info": colors.HexColor("#1E8E5A")}

_LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "mark-header.png")

_REPL = {"→": "->", "✓": "v", "→": "->", "•": "-", " ": " ", " ": " ", " ": " ", "​": ""}


def _c(text):
    """Texte sûr pour Paragraph : remplace les caractères hors WinAnsi, échappe le XML."""
    s = str(text if text is not None else "")
    for k, v in _REPL.items():
        s = s.replace(k, v)
    s = s.encode("cp1252", "replace").decode("cp1252")
    return escape(s)


def _raw(text):
    """Texte brut compatible WinAnsi (pour canvas.drawString : pas d'échappement XML)."""
    s = str(text or "")
    for k, v in _REPL.items():
        s = s.replace(k, v)
    return s.encode("cp1252", "replace").decode("cp1252")


OVERALL_LABELS = {
    "fr": {"eleve": "Risque élevé", "moyen": "Risque moyen", "faible": "Risque faible"},
    "en": {"eleve": "High risk", "moyen": "Medium risk", "faible": "Low risk"},
}


def _reflow(text):
    import contract_engine
    return contract_engine.reflow_text(text or "")


def _short(text, n=90):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


def _styles():
    ss = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK, alignment=TA_LEFT)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=11, textColor=SOFT),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=BURGUNDY, spaceBefore=14, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=6),
        "quote": ParagraphStyle("quote", parent=base, fontName="Helvetica-Oblique", fontSize=8.8, leading=12, textColor=SOFT, leftIndent=8),
        "mono": ParagraphStyle("mono", parent=base, fontName="Courier", fontSize=7.8, leading=10),
        "body": ParagraphStyle("body", parent=base, fontSize=10, leading=14.5),
        "art": ParagraphStyle("art", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=14.5, spaceBefore=8),
    }


def _doc(buf, title, footer_left):
    def decorate(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(colors.HexColor("#0B1E36"))
        canvas.rect(0, h - 14 * mm, w, 14 * mm, stroke=0, fill=1)
        x0 = 18 * mm
        if os.path.exists(_LOGO):
            try:
                canvas.drawImage(_LOGO, x0, h - 12.2 * mm, width=11 * mm, height=8.4 * mm, mask="auto", preserveAspectRatio=True)
                x0 += 13.5 * mm
            except Exception:  # noqa: BLE001 — le logo ne doit jamais empêcher la génération
                pass
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(x0, h - 9 * mm, "Massey Contracts & Tax")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(w - 18 * mm, h - 9 * mm, "Contract Intelligence")
        canvas.setFillColor(SOFT)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(18 * mm, 10 * mm, _raw(footer_left)[:110])
        canvas.drawRightString(w - 18 * mm, 10 * mm, "%d" % doc.page)
        canvas.restoreState()
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=24 * mm, bottomMargin=18 * mm,
                             title=_raw(title), author="Massey Contracts & Tax", invariant=1), decorate


def _badge(level, label):
    t = Table([[Paragraph('<font color="white"><b>%s</b></font>' % _c(label.upper()), ParagraphStyle("b", fontName="Helvetica-Bold", fontSize=7, leading=8))]],
              colWidths=None)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LEVEL_COLORS.get(level, SOFT)), ("LEFTPADDING", (0, 0), (-1, -1), 5),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return t


def analysis_report(title, result, lang, party_groups, obligations, created_at, level_labels):
    en = lang == "en"
    S = _styles()
    buf = io.BytesIO()
    doc, deco = _doc(buf, title, ("Analysis report - " if en else "Rapport d'analyse - ") + title)
    story = [Paragraph(_c(title), S["h1"]),
             Paragraph(_c(("Analysis report - generated on " if en else "Rapport d'analyse - généré le ") + (created_at or "")[:10]
                          + (" - %d sections, %d words" % (result.get("clause_count", 0), result.get("word_count", 0)) if en
                             else " - %d clauses, %d mots" % (result.get("clause_count", 0), result.get("word_count", 0)))), S["small"]),
             Spacer(1, 6), HRFlowable(width="100%", thickness=0.6, color=LINE), Spacer(1, 6)]
    summ = result.get("summary", {})
    counts = summ.get("counts", {})
    overall = summ.get("overall")
    story.append(Paragraph(_c(("Overall level: " if en else "Niveau global : ") + OVERALL_LABELS["en" if en else "fr"].get(overall, str(overall))
                              + "  |  " + ("High %d, medium %d, notes %d" % (counts.get("eleve", 0), counts.get("moyen", 0), counts.get("info", 0)) if en
                                         else "Élevé %d, moyen %d, à noter %d" % (counts.get("eleve", 0), counts.get("moyen", 0), counts.get("info", 0)))), S["h3"]))

    def finding_block(f, missing=False):
        parts = [Paragraph("<b>%s</b>" % _c(f.get("topic", "")), S["base"])]
        where = ""
        if not missing:
            where = ("Section " if en else "Clause ") + str(f.get("clause_number", "")) + (" - " + _short(f.get("clause_title", "")) if f.get("clause_title") else "")
        if where:
            parts.append(Paragraph(_c(where), S["small"]))
        if f.get("excerpt"):
            parts.append(Paragraph("&laquo; %s &raquo;" % _c(f["excerpt"]), S["quote"]))
        parts.append(Paragraph("<b>%s</b> %s" % (_c("Why it matters:" if en else "Pourquoi c'est important :"), _c(f.get("why", ""))), S["base"]))
        parts.append(Paragraph("<b>%s</b> %s" % (_c("Suggested fix:" if en else "Piste de correction :"), _c(f.get("fix", ""))), S["base"]))
        lvl = f.get("level", "info")
        head = Table([[_badge(lvl, level_labels.get(lvl, lvl)), parts[0]]], colWidths=[22 * mm, None])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        return KeepTogether([head] + parts[1:] + [Spacer(1, 7)])

    story.append(Paragraph(_c("Points to review (%d)" % len(result.get("findings", [])) if en else "Points à examiner (%d)" % len(result.get("findings", []))), S["h2"]))
    if result.get("findings"):
        for f in result["findings"]:
            story.append(finding_block(f))
    else:
        story.append(Paragraph(_c("No risky wording detected by the rules." if en else "Aucune formulation à risque détectée par les règles."), S["base"]))
    story.append(Paragraph(_c("Possibly missing clauses (%d)" % len(result.get("missing", [])) if en else "Clauses possiblement absentes (%d)" % len(result.get("missing", []))), S["h2"]))
    for f in result.get("missing", []):
        story.append(finding_block(f, missing=True))

    pb = result.get("playbook")
    if pb:
        story.append(Paragraph(_c(("Playbook: " if en else "Playbook : ") + pb.get("name", "")), S["h2"]))
        if pb.get("compliant"):
            story.append(Paragraph(_c("Compliant with your positions." if en else "Conforme à vos positions."), S["base"]))
        for d in pb.get("deviations", []):
            line = d.get("topic") or ""
            if d.get("type") == "payment_days":
                line = ("Payment term %s days (max %s)" if en else "Délai de paiement %s jours (max %s)") % (d.get("value"), d.get("limit"))
            elif d.get("type") == "notice_days":
                line = ("Notice %s days (min %s)" if en else "Préavis %s jours (min %s)") % (d.get("value"), d.get("limit"))
            elif d.get("type") == "forbid":
                line = ("Never accepted: " if en else "Position « jamais » : ") + line
            elif d.get("type") == "require":
                line = ("Required clause missing: " if en else "Clause exigée absente : ") + line
            story.append(Paragraph("- " + _c(line) + (" (%s)" % _c(d["note"]) if d.get("note") else ""), S["base"]))

    if party_groups:
        story.append(Paragraph(_c("Obligations by party" if en else "Obligations par partie"), S["h2"]))
        for party, items in party_groups:
            story.append(Paragraph(_c((party if party != "—" else ("Unassigned" if en else "Non attribué")) + " (%d)" % len(items)), S["h3"]))
            for o in items[:25]:
                story.append(Paragraph("- " + _c(o["action"]) + _c("  [%s %s]" % ("Section" if en else "Clause", o["clause_number"])), S["base"]))

    if obligations:
        story.append(Paragraph(_c("Dates and deadlines spotted" if en else "Dates et délais repérés"), S["h2"]))
        rows = [[_c("Type"), _c("Passage"), _c("Delay" if en else "Délai"), _c("Date")]]
        for o in obligations[:30]:
            rows.append([Paragraph(_c(o.get("label") or o.get("kind", "")), S["small"]), Paragraph(_c(o.get("excerpt") or ""), S["small"]),
                         Paragraph(_c(o.get("delay_text") or ""), S["small"]), Paragraph(_c(o.get("due_date") or ""), S["small"])])
        t = Table(rows, colWidths=[24 * mm, 94 * mm, 28 * mm, 26 * mm], repeatRows=1)
        t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 8), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)

    story += [Spacer(1, 14), HRFlowable(width="100%", thickness=0.6, color=LINE),
              Paragraph(_c("Automated rule-based analysis: it guides the review and is not legal advice. Have a lawyer review the contract before signing." if en
                           else "Analyse automatisée par règles : elle oriente la relecture et ne constitue pas un avis juridique. Faites relire le contrat par un avocat avant de le signer."), S["small"])]
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()


def signed_contract(title, body, lang, body_sha, signers, request_ref, completed_at, verify_url=None):
    """PDF final : texte exact du contrat puis page de certificat de signature."""
    en = lang == "en"
    S = _styles()
    buf = io.BytesIO()
    doc, deco = _doc(buf, title, "%s - %s %s" % (title, "signature ref." if en else "réf. signature", request_ref))
    story = [Paragraph(_c(title), S["h1"]), Spacer(1, 6)]
    for raw in _reflow(body).split("\n"):
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 5))
        elif line.lower().startswith(("article ", "clause ", "section ")) and len(line) < 120:
            story.append(Paragraph(_c(line), S["art"]))
        else:
            story.append(Paragraph(_c(line), S["body"]))
    story.append(Spacer(1, 18))
    cert = [HRFlowable(width="100%", thickness=1.2, color=BURGUNDY),
            Paragraph(_c("Signature certificate" if en else "Certificat de signature"), S["h2"]),
            Paragraph(_c(("Reference: " if en else "Référence : ") + str(request_ref) + ("  |  Completed: " if en else "  |  Terminé : ") + (completed_at or "")[:19].replace("T", " ") + " UTC"), S["small"]),
            Spacer(1, 4),
            Paragraph("<b>%s</b>" % _c("SHA-256 of the contract text above:" if en else "Empreinte SHA-256 du texte du contrat ci-dessus :"), S["small"]),
            Paragraph(_c(body_sha), S["mono"]), Spacer(1, 8)]
    rows = [[_c("Signatory" if en else "Signataire"), _c("Legal name typed" if en else "Nom légal saisi"), _c("Signed (UTC)" if en else "Signé (UTC)"), "IP"]]
    for s in signers:
        rows.append([Paragraph(_c("%s<%s>" % ((s["name"] + " ") if s["name"] else "", s["email"])), S["small"]),
                     Paragraph("<b>%s</b>" % _c(s["signed_name"] or ""), S["small"]),
                     Paragraph(_c((s["signed_at"] or "")[:19].replace("T", " ")), S["small"]),
                     Paragraph(_c(s["ip"] or ""), S["small"])])
    t = Table(rows, colWidths=[52 * mm, 48 * mm, 40 * mm, 32 * mm], repeatRows=1)
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 8), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    cert.append(t)
    cert.append(Spacer(1, 10))
    ids = [s for s in signers if s.get("identity")]
    if ids:
        cert.append(Paragraph("<b>%s</b>" % _c("Identity check (live ID photo + selfie, reviewed by the sender)" if en else "Vérification d'identité (photo en direct de la pièce + selfie, contrôlés par l'expéditeur)"), S["small"]))
        for s_ in ids:
            cert.append(Paragraph(_c("%s : %s" % (s_["email"], s_["identity"])), S["small"]))
        cert.append(Paragraph(_c("The images are not part of this document and are deleted after the retention period." if en else "Les images ne figurent pas dans ce document et sont supprimées après la durée de conservation."), S["small"]))
        cert.append(Spacer(1, 8))
    if verify_url:
        try:
            import qrcode
            from reportlab.platypus import Image
            qr = qrcode.QRCode(box_size=4, border=1)
            qr.add_data(verify_url)
            qr.make(fit=True)
            qbuf = io.BytesIO()
            qr.make_image(fill_color="black", back_color="white").save(qbuf, format="PNG")
            qbuf.seek(0)
            row = Table([[Image(qbuf, width=24 * mm, height=24 * mm),
                          [Paragraph("<b>%s</b>" % _c("Verify this document" if en else "Vérifier ce document"), S["small"]),
                           Paragraph(_c(("Scan the code or open the address below, then drop this PDF: the site tells you whether it matches the signed original." if en
                                         else "Scannez le code ou ouvrez l'adresse ci-dessous, puis déposez ce PDF : le site vous dit s'il correspond à l'original signé.")), S["small"]),
                           Paragraph(_c(verify_url), S["mono"])]]], colWidths=[28 * mm, None])
            row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
            cert.append(row)
            cert.append(Spacer(1, 8))
        except Exception:  # noqa: BLE001 — le QR est un plus, jamais bloquant
            pass
    cert.append(Paragraph(_c(
        "Each signatory received a private link by email, typed their full legal name and ticked two consent boxes. The server recorded the time, IP address and browser of each signature. "
        "This is an internal attestation with an audit trail, not a certified electronic signature issued by a trusted third party: its evidential value is not guaranteed by an external provider."
        if en else
        "Chaque signataire a reçu un lien privé par courriel, saisi son nom légal complet et coché deux cases de consentement. Le serveur a enregistré l'heure, l'adresse IP et le navigateur de chaque signature. "
        "Il s'agit d'une attestation interne avec piste d'audit, et non d'une signature électronique certifiée par un tiers de confiance : sa valeur probante n'est pas garantie par un fournisseur externe."), S["small"]))
    story.append(KeepTogether(cert))
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()


def contract_for_signature(title, body, lang, body_sha, signers):
    """PDF envoyé au fournisseur de signature certifiée : texte du contrat puis page de signatures
    avec des ancres invisibles (/sigN/, /datN/) où le fournisseur pose les zones de signature."""
    en = lang == "en"
    S = _styles()
    buf = io.BytesIO()
    doc, deco = _doc(buf, title, "%s - SHA-256 %s" % (title, body_sha[:16]))
    story = [Paragraph(_c(title), S["h1"]), Spacer(1, 6)]
    for raw in _reflow(body).split("\n"):
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 5))
        elif line.lower().startswith(("article ", "clause ", "section ")) and len(line) < 120:
            story.append(Paragraph(_c(line), S["art"]))
        else:
            story.append(Paragraph(_c(line), S["body"]))
    from reportlab.platypus import PageBreak
    story += [PageBreak(), Paragraph(_c("Signatures"), S["h2"]),
              Paragraph(_c(("Text fingerprint (SHA-256): " if en else "Empreinte du texte (SHA-256) : ") + body_sha), S["mono"]), Spacer(1, 14)]
    white = ParagraphStyle("anchor", fontName="Helvetica", fontSize=6, leading=7, textColor=colors.white)
    for i, sg in enumerate(signers, start=1):
        label = "%s <%s>" % (sg.get("name") or "", sg["email"]) if sg.get("name") else sg["email"]
        story.append(KeepTogether([
            Paragraph("<b>%s</b>" % _c(label), S["base"]),
            Spacer(1, 22), Paragraph("/sig%d/" % i, white),
            HRFlowable(width="60%", thickness=0.6, color=INK, hAlign="LEFT"),
            Paragraph(_c("Signature"), S["small"]), Spacer(1, 10),
            Paragraph("/dat%d/" % i, white), HRFlowable(width="30%", thickness=0.6, color=INK, hAlign="LEFT"),
            Paragraph(_c("Date"), S["small"]), Spacer(1, 18)]))
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()


def contract_document(title, body, lang, subtitle=None, parties=None):
    """PDF de lecture du contrat : titre, intertitres en gras, texte justifié, bloc de signatures, pagination."""
    import contract_engine
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    en = lang == "en"
    S = _styles()
    just = ParagraphStyle("just", parent=S["body"], fontName="Times-Roman", fontSize=10.8, leading=15.5, alignment=TA_JUSTIFY, spaceAfter=5)
    head = ParagraphStyle("head", parent=just, spaceBefore=7, keepWithNext=0)
    ttl = ParagraphStyle("ttl", parent=just, fontName="Times-Bold", fontSize=11, spaceBefore=12, keepWithNext=1, alignment=TA_LEFT)
    big = ParagraphStyle("big", parent=ttl, fontSize=16, leading=20, alignment=TA_CENTER, spaceBefore=4, spaceAfter=3, textTransform="uppercase")
    sub = ParagraphStyle("sub", parent=S["small"], alignment=TA_CENTER, fontName="Helvetica-Oblique")
    buf = io.BytesIO()
    doc, deco = _doc(buf, title, title)
    story = [Paragraph(_c(title), big)]
    if subtitle:
        story.append(Paragraph(_c(subtitle), sub))
    story += [HRFlowable(width="100%", thickness=1.2, color=BURGUNDY, spaceBefore=4, spaceAfter=10)]
    for blk in contract_engine.render_blocks(body):
        k = blk[0]
        if k == "blank":
            story.append(Spacer(1, 3))
        elif k == "title":
            story.append(Paragraph(_c(blk[1]), ttl))
        elif k == "head":
            lead = ("%s. %s" % (blk[1], blk[2])).strip()
            story.append(Paragraph("<b>%s</b>%s" % (_c(lead), (" " + _c(blk[3])) if blk[3] else ""), head))
        else:
            story.append(Paragraph(_c(blk[1]), just))
    names = [x for x in (parties or []) if x] or (["Party A", "Party B"] if en else ["Partie A", "Partie B"])
    cells = []
    for nm in names[:6]:
        cells.append([Paragraph("<b>%s</b>" % _c(nm), S["base"]), Spacer(1, 30), HRFlowable(width="85%", thickness=0.6, color=INK, hAlign="LEFT"),
                      Paragraph("Signature / Date", S["small"])])
    rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
    for r in rows:
        if len(r) == 1:
            r.append("")
    tbl = Table(rows, colWidths=[85 * mm, 85 * mm])
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 14)]))
    story += [Spacer(1, 16), KeepTogether([Paragraph(_c("Made in as many originals as there are parties, on ____ / ____ / ________." if en else "Fait en autant d'exemplaires que de parties, le ____ / ____ / ________."), S["small"]), Spacer(1, 6), tbl])]
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()
