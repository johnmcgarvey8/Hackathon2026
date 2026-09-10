const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const moduleRoot = process.env.DOCX_MODULE_ROOT;
const load = moduleRoot ? createRequire(path.join(moduleRoot, "document-loader.cjs")) : require;
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, HeadingLevel, WidthType, ShadingType, BorderStyle, Footer, PageNumber, AlignmentType, ExternalHyperlink } = load("docx");

const source = path.join(__dirname, "geo-agent-build-plan.md");
const output = path.join(__dirname, "geo-agent-build-plan.docx");
const lines = fs.readFileSync(source, "utf8").split(/\r?\n/);
const children = [];
const contentWidth = 9630;

function runs(text) {
  const result = [];
  const pattern = /\[([^\]]+)\]\(([^)]+)\)|(https:\/\/[^\s]+)/g;
  let previous = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > previous) result.push(new TextRun(text.slice(previous, match.index)));
    const target = match[2] || match[3];
    result.push(new ExternalHyperlink({ link: target, children: [new TextRun({ text: match[1] || target, style: "Hyperlink" })] }));
    previous = match.index + match[0].length;
  }
  if (previous < text.length) result.push(new TextRun(text.slice(previous)));
  return result;
}

function addTable(tableLines) {
  const rows = tableLines.filter(line => !/^\|\s*---/.test(line)).map(line => line.slice(1, -1).split("|").map(cell => cell.trim()));
  const columns = rows[0].length;
  const widths = columns === 3 ? [1800, 3930, 3900] : [700, 4250, 2200, 2480];
  if (widths.length !== columns || widths.reduce((sum, width) => sum + width, 0) !== contentWidth) throw new Error("Invalid table dimensions");
  children.push(new Table({
    width: { size: contentWidth, type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((row, index) => new TableRow({
      tableHeader: index === 0,
      cantSplit: true,
      children: row.map((text, column) => new TableCell({
        width: { size: widths[column], type: WidthType.DXA },
        shading: { fill: index === 0 ? "E5EFF8" : "FFFFFF", type: ShadingType.CLEAR },
        borders: Object.fromEntries(["top", "bottom", "left", "right"].map(side => [side, { style: BorderStyle.SINGLE, size: 4, color: "D5DDE4" }])),
        margins: { top: 100, bottom: 100, left: 100, right: 100 },
        children: [new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text, bold: index === 0, size: 19 })] })]
      }))
    }))
  }));
  children.push(new Paragraph({ spacing: { after: 100 }, children: [] }));
}

for (let index = 0; index < lines.length; index += 1) {
  const line = lines[index];
  if (!line.trim()) continue;
  if (line.startsWith("|")) {
    const tableLines = [];
    while (index < lines.length && lines[index].startsWith("|")) tableLines.push(lines[index++]);
    index -= 1;
    addTable(tableLines);
    continue;
  }
  const heading = /^(#{1,3}) (.*)$/.exec(line);
  if (heading) {
    const level = [HeadingLevel.TITLE, HeadingLevel.HEADING_1, HeadingLevel.HEADING_2][heading[1].length - 1];
    children.push(new Paragraph({ heading: level, keepNext: true, children: runs(heading[2]) }));
  } else if (line.startsWith("- ")) {
    children.push(new Paragraph({ bullet: { level: 0 }, spacing: { after: 110 }, children: runs(line.slice(2)) }));
  } else {
    children.push(new Paragraph({ spacing: { after: 130 }, children: runs(line) }));
  }
}

const document = new Document({
  title: "GEO Optimiser: Live Agent Build Plan",
  subject: "Implementation plan based on the GEO Optimiser HTML proposal",
  description: "Planning only. Live GEO agent, verified evidence, Markdown hand-off and downstream proof.",
  styles: {
    default: { document: { run: { font: "Arial", size: 21, color: "242424" }, paragraph: { spacing: { line: 276 } } } },
    paragraphStyles: [
      { id: "Title", name: "Title", basedOn: "Normal", run: { size: 38, bold: true, color: "242424" }, paragraph: { spacing: { before: 160, after: 240 } } },
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 29, bold: true, color: "0067B8" }, paragraph: { outlineLevel: 0, spacing: { before: 300, after: 150 }, keepNext: true } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 24, bold: true, color: "242424" }, paragraph: { outlineLevel: 1, spacing: { before: 240, after: 120 }, keepNext: true } }
    ]
  },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1080, bottom: 1080, left: 1138, right: 1138 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: "GEO Optimiser | Planning only | ", size: 17, color: "5C5C5C" }), new TextRun({ children: [PageNumber.CURRENT], size: 17 })] })] }) },
    children
  }]
});

Packer.toBuffer(document).then(buffer => {
  fs.writeFileSync(output, buffer);
  console.log(`Created ${path.basename(output)} (${buffer.length} bytes) from ${path.basename(source)}.`);
}).catch(error => {
  console.error(error);
  process.exitCode = 1;
});