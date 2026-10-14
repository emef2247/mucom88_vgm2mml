"""Install the pinned official mucomDotNET multi-port PCM tool profile locally."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
REVISION='f0403762a37028c77afe23193bd674d7bf6efaf7'
ARCHIVE_SHA256='666bc13830a6a9d4748a8f6b9c7c0317f59f2732b7bc12b6c25209600138f2dd'
BASE='https://raw.githubusercontent.com/kuma4649/mucomDotNET/'+REVISION+'/'


def fetch(name):
    request=urllib.request.Request(BASE+name,headers={'User-Agent':'mucom88-vgm2mml-tool-setup'})
    with urllib.request.urlopen(request,timeout=60) as response:return response.read()


def main():
    destination=ROOT/'.tools/mucomdotnet-pcm'
    archive=destination/'bin.zip'
    data=archive.read_bytes() if archive.is_file() else fetch('bin.zip')
    if hashlib.sha256(data).hexdigest()!=ARCHIVE_SHA256:
        raise ValueError('mucomDotNET archive hash mismatch; existing files preserved')
    # Read the license before unpacking. Tools remain external, ignored assets.
    license_text=fetch('LICENSE.txt')
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        for entry in bundle.infolist():
            if not (destination/entry.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError('Archive member escapes the tool directory')
            path=destination/entry.filename
            if not entry.is_dir() and path.is_file() and path.read_bytes()!=bundle.read(entry):
                raise ValueError('Refusing to overwrite modified tool file: '+str(path))
        destination.mkdir(parents=True,exist_ok=True)
        bundle.extractall(destination)
    archive.write_bytes(data)
    (destination/'LICENSE.txt').write_bytes(license_text)
    (destination/'source.json').write_text(json.dumps(dict(
        repository='https://github.com/kuma4649/mucomDotNET',revision=REVISION,
        url=BASE+'bin.zip',sha256=ARCHIVE_SHA256),indent=2)+'\n',encoding='utf-8')
    print('Installed PCM compiler/replay profile: '+str(destination/'output/NET6'))
    print('Requires Windows .NET 6 runtime; ordinary MUC retains the existing compiler profile.')


if __name__=='__main__':main()
