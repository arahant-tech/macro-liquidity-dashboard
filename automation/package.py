"""Export only the explicit public page/data allowlist after integrity checks."""
from pathlib import Path
import json,shutil
from update import ROOT,load,validate_model,digest,require

def package():
    model=load(ROOT/'live-model.json');status=load(ROOT/'update-status.json')
    validate_model(model)
    require(digest((ROOT/'live-model.json').read_bytes())==status['model_sha256'],'Published model/status hash mismatch')
    require(model['generated_at_utc']==status['model_generated_at_utc'],'Published model/status time mismatch')
    target=ROOT/'_site'
    if target.exists():shutil.rmtree(target)
    target.mkdir()
    files=load(ROOT/'automation/public-files.json')+['live-model.json','update-status.json']
    for relative in files:
        source=ROOT/relative;destination=target/relative
        require(source.resolve().is_relative_to(ROOT) and source.is_file() and not source.is_symlink(),'Invalid public artifact path')
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
    print(f'Validated {len(files)} public files; code and raw acquisition artifacts excluded from page package.')
if __name__=='__main__':package()
