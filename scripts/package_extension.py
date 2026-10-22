"""Build an installable, dependency-free VSIX with the current Python engine."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def package(output=None):
    source = ROOT / 'vscode-extension'
    manifest = json.loads((source / 'package.json').read_text())
    version = manifest['version']
    if output is None:
        output = ROOT / 'dist' / f"{manifest['name']}-{version}.vsix"
    vsix_manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
<Metadata><Identity Language="en-US" Id="{manifest['name']}" Version="{version}" Publisher="knitcode-local"/><DisplayName>{manifest['displayName']}</DisplayName><Description xml:space="preserve">Python code relationships and Git evidence</Description><Tags>python,visualization</Tags><Categories>Visualization</Categories><GalleryFlags>Public</GalleryFlags><Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.85.0"/><Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace"/></Properties></Metadata>
<Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
<Dependencies/><Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/></Assets></PackageManifest>'''
    content_types = '''<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="json" ContentType="application/json"/><Default Extension="js" ContentType="application/javascript"/><Default Extension="css" ContentType="text/css"/><Default Extension="py" ContentType="text/plain"/><Default Extension="md" ContentType="text/markdown"/><Default Extension="vsixmanifest" ContentType="text/xml"/></Types>'''
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('extension.vsixmanifest', vsix_manifest)
        archive.writestr('[Content_Types].xml', content_types)
        for relative in ['package.json', 'extension.js', 'README.md', 'media/map.js', 'media/map.css']:
            archive.write(source / relative, 'extension/' + relative)
        for file in sorted(ROOT.glob('*.py')):
            archive.write(file, 'extension/vendor/' + file.name)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    print(package(parser.parse_args().output).resolve())
