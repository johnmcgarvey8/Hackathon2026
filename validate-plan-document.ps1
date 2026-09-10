$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead((Join-Path $PSScriptRoot 'geo-agent-build-plan.docx'))
try {
    $parsedCount = 0
    foreach ($entry in $archive.Entries) {
        if ($entry.FullName -notmatch '\.(xml|rels)$') { continue }
        $member = [xml]::new()
        $stream = $entry.Open()
        try { $member.Load($stream) } finally { $stream.Dispose() }
        $parsedCount++
    }
    $document = [xml]::new()
    $stream = $archive.GetEntry('word/document.xml').Open()
    try { $document.Load($stream) } finally { $stream.Dispose() }
    $namespaceUri = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    $namespaces = [Xml.XmlNamespaceManager]::new($document.NameTable)
    $namespaces.AddNamespace('w', $namespaceUri)
    $headers = @($document.SelectNodes('//w:tblHeader', $namespaces) | Where-Object {
        $_.GetAttribute('val', $namespaceUri) -notin @('false', '0', 'off')
    })
    if ($headers.Count -ne 3) { throw "Expected 3 effective headers, found $($headers.Count)" }
    $firstTable = $document.SelectSingleNode('//w:tbl', $namespaces)
    $rows = @($firstTable.SelectNodes('./w:tr/w:tc[1]', $namespaces) | ForEach-Object {
        ($_.SelectNodes('.//w:t', $namespaces) | ForEach-Object InnerText) -join ''
    })
    $expected = @('Section', 'Startle', 'WIIFM', 'Needs and Challenges', 'Define Questions', 'Cornerstone', 'Supporting Evidence 1', 'Supporting Evidence 2', 'Supporting Evidence 3', 'Repeat Cornerstone', 'Conclusion and next steps')
    if (($rows -join '|') -ne ($expected -join '|')) { throw 'Planning table row mismatch' }
    $paragraphs = @($document.SelectNodes('//w:p', $namespaces) | ForEach-Object {
        ($_.SelectNodes('.//w:t', $namespaces) | ForEach-Object InnerText) -join ''
    })
    $markdown = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'geo-agent-build-plan.md')
    $headings = @([regex]::Matches($markdown, '(?m)^#{1,3} (.+)$') | ForEach-Object { $_.Groups[1].Value.Trim() })
    foreach ($heading in $headings) {
        if ($paragraphs -notcontains $heading) { throw "Missing heading: $heading" }
    }
    [pscustomobject]@{
        Status = 'PASS'
        XmlMembersParsed = $parsedCount
        EffectiveTableHeaders = $headers.Count
        PlanningRows = $rows.Count - 1
        MatchedHeadings = $headings.Count
        VisualRendering = 'Not available; structural validation only'
    } | ConvertTo-Json
} finally {
    $archive.Dispose()
}