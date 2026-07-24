from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class MeetingPdfRenderer:
    def render(self, report):
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=14 * mm,
            leftMargin=14 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title=report["payload"]["title"],
            author="Codenoa Sales Coach",
        )
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="CenterSmall", parent=styles["BodyText"], alignment=TA_CENTER, fontSize=8))
        styles.add(ParagraphStyle(name="Evidence", parent=styles["BodyText"], fontSize=8.2, leading=10))
        story = self._general(report, styles)
        if report["report_type"] == "seller_packet":
            for seller in report["payload"].get("seller_pages", []):
                story.extend([PageBreak(), *self._seller_page(seller, report, styles)])
        doc.build(story, onFirstPage=self._footer(report), onLaterPages=self._footer(report))
        return buffer.getvalue()

    def _general(self, report, styles):
        payload = report["payload"]
        kpis = payload["kpis"]
        story = [
            Paragraph(escape(str(payload["title"])), styles["Title"]),
            Paragraph(
                f"Período {report['period']['start']} a {report['period']['end']} · "
                f"Informe {report['report_id']} · versión {report['version']}",
                styles["CenterSmall"],
            ),
            Spacer(1, 5 * mm),
            self._kpi_table(kpis, styles),
            Spacer(1, 5 * mm),
            Paragraph("Resumen ejecutivo", styles["Heading2"]),
            *[Paragraph(text, styles["BodyText"]) for text in payload["executive_summary"]],
            Spacer(1, 4 * mm),
            Paragraph("Venta por vendedor", styles["Heading2"]),
            self._sales_chart(payload.get("ranking", [])),
            Spacer(1, 3 * mm),
            Paragraph("Ranking comercial", styles["Heading2"]),
            self._ranking(payload.get("ranking", []), styles),
            Spacer(1, 4 * mm),
            Paragraph("Alertas y oportunidades", styles["Heading2"]),
            self._alerts(payload.get("alerts", []), styles),
        ]
        recognitions = payload.get("recognitions", [])
        if recognitions:
            story.extend(
                [
                    Spacer(1, 3 * mm),
                    Paragraph("Reconocimientos", styles["Heading2"]),
                    *[
                        Paragraph(
                            f"• <b>{escape(str(item['seller']))}</b>: "
                            f"{escape(str(item['message']))}.",
                            styles["BodyText"],
                        )
                        for item in recognitions
                    ],
                ]
            )
        return story

    def _seller_page(self, seller, report, styles):
        name = seller["identification"]["name"]
        kpis = seller["kpis"]
        story = [
            Paragraph(f"Ficha individual — {escape(str(name))}", styles["Title"]),
            Paragraph(
                f"{escape(str(seller['identification'].get('sales_force') or 'Sin fuerza'))} · "
                f"{escape(str(seller['identification']['period']))}",
                styles["CenterSmall"],
            ),
            Spacer(1, 4 * mm),
            self._kpi_table(
                {
                    "net_sales": kpis.get("sales", 0),
                    "quantity": kpis.get("quantity", 0),
                    "active_clients": kpis.get("clients", 0),
                    "growth_pct": kpis.get("growthPct", 0),
                    "objective_fulfillment_pct": (
                        seller["objectives"][0].get("progress", {}).get("fulfillment_pct", 0)
                        if seller.get("objectives") else 0
                    ),
                    "opportunity_potential": 0,
                },
                styles,
            ),
            Spacer(1, 4 * mm),
            self._evidence_section("Fortalezas", seller.get("strengths", []), styles),
            Spacer(1, 3 * mm),
            self._evidence_section("Oportunidades", seller.get("opportunities", []), styles),
            Spacer(1, 3 * mm),
            self._evidence_section("Plan de acción", seller.get("action_plan", []), styles),
            Spacer(1, 4 * mm),
            Paragraph("Compromiso de la reunión", styles["Heading2"]),
            Table(
                [["Acción", "Responsable", "Fecha", "Resultado"], ["", "", "", ""], ["", "", "", ""]],
                colWidths=[65 * mm, 38 * mm, 28 * mm, 45 * mm],
                rowHeights=[7 * mm, 14 * mm, 14 * mm],
                style=self._grid_style(),
            ),
        ]
        return story

    def _kpi_table(self, kpis, styles):
        cells = [
            ("Venta neta", self._money(kpis.get("net_sales"))),
            ("Cantidad", self._number(kpis.get("quantity"))),
            ("Clientes activos", self._number(kpis.get("active_clients"))),
            ("Crecimiento", f"{self._number(kpis.get('growth_pct'))}%"),
            ("Cumplimiento", f"{self._number(kpis.get('objective_fulfillment_pct'))}%"),
            ("Potencial", self._money(kpis.get("opportunity_potential"))),
        ]
        data = [
            [Paragraph(f"<b>{label}</b><br/>{value}", styles["CenterSmall"]) for label, value in cells[:3]],
            [Paragraph(f"<b>{label}</b><br/>{value}", styles["CenterSmall"]) for label, value in cells[3:]],
        ]
        table = Table(data, colWidths=[58 * mm] * 3, rowHeights=[17 * mm] * 2)
        table.setStyle(self._grid_style(background=colors.HexColor("#eef5f2")))
        return table

    def _sales_chart(self, rows):
        selected = rows[:8]
        drawing = Drawing(175 * mm, 48 * mm)
        if not selected:
            drawing.add(String(10, 50, "Sin datos para graficar", fontSize=8))
            return drawing
        chart = VerticalBarChart()
        chart.x = 8 * mm
        chart.y = 10 * mm
        chart.width = 158 * mm
        chart.height = 32 * mm
        chart.data = [[float(row.get("sales") or 0) for row in selected]]
        chart.categoryAxis.categoryNames = [
            str(row.get("seller") or "")[:12] for row in selected
        ]
        chart.categoryAxis.labels.fontSize = 6
        chart.categoryAxis.labels.angle = 20
        chart.valueAxis.labels.fontSize = 6
        chart.bars[0].fillColor = colors.HexColor("#2d7d61")
        chart.barSpacing = 2
        drawing.add(chart)
        return drawing

    def _ranking(self, rows, styles):
        data = [["#", "Vendedor", "Venta", "Crec.", "Clientes", "Mix"]]
        for row in rows[:10]:
            data.append([
                row.get("rankSales", ""),
                Paragraph(escape(str(row.get("seller") or "")), styles["Evidence"]),
                self._money(row.get("sales")),
                f"{self._number(row.get('growthPct'))}%",
                self._number(row.get("clients")),
                self._number(row.get("mixCount")),
            ])
        table = Table(data, colWidths=[10 * mm, 54 * mm, 36 * mm, 24 * mm, 25 * mm, 22 * mm], repeatRows=1)
        table.setStyle(self._grid_style(header=True))
        return table

    def _alerts(self, rows, styles):
        data = [["Prioridad", "Evidencia", "Responsable", "Estado"]]
        for row in rows[:10]:
            data.append([
                row.get("severity", ""),
                Paragraph(escape(str(row.get("message") or "")), styles["Evidence"]),
                escape(str((row.get("assignee") or {}).get("name", ""))),
                row.get("status", ""),
            ])
        table = Table(data, colWidths=[24 * mm, 92 * mm, 38 * mm, 25 * mm], repeatRows=1)
        table.setStyle(self._grid_style(header=True))
        return table

    def _evidence_section(self, title, rows, styles):
        content = [Paragraph(title, styles["Heading2"])]
        if not rows:
            content.append(Paragraph("Sin observaciones para este período.", styles["BodyText"]))
        for item in rows[:3]:
            message = item.get("message") or item.get("text") or item.get("action") or str(item)
            evidence = item.get("evidence") or {}
            content.append(
                Paragraph(
                    f"• {escape(str(message))}<br/><font size='7'>"
                    f"Evidencia: {escape(str(evidence))}</font>",
                    styles["Evidence"],
                )
            )
        return KeepTogether(content)

    @staticmethod
    def _grid_style(background=None, header=False):
        commands = [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aaba4")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        if background:
            commands.append(("BACKGROUND", (0, 0), (-1, -1), background))
        if header:
            commands.extend([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174b3a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ])
        return TableStyle(commands)

    @staticmethod
    def _footer(report):
        def draw(canvas, doc):
            canvas.saveState()
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(colors.grey)
            canvas.drawString(14 * mm, 8 * mm, f"Actualización: {report.get('data_updated_at') or 'sin dato'}")
            canvas.drawRightString(196 * mm, 8 * mm, f"Página {doc.page} · {report['report_id'][:8]} v{report['version']}")
            canvas.restoreState()
        return draw

    @staticmethod
    def _money(value):
        return f"$ {float(value or 0):,.2f}"

    @staticmethod
    def _number(value):
        return f"{float(value or 0):,.1f}"
