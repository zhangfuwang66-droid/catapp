"""运维用签名固件发布。仅复制已签名的公开程序包，不接收或存储签名私钥。"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from firmware_manifest import read_package


def publish(source, target, public_key):
    key=public_key.read_bytes()
    manifest=read_package(source,key)
    target.mkdir(parents=True,exist_ok=True)
    destination=target/manifest['id']
    if destination.exists():
        if read_package(destination,key)!=manifest:
            raise ValueError('发布编号已存在且内容不同，不能覆盖')
        return {'ok':True,'release_id':manifest['id'],'already_published':True}
    with tempfile.TemporaryDirectory(prefix='.incoming-',dir=target) as temp:
        stage=Path(temp)/manifest['id']; stage.mkdir(mode=0o700)
        for name in ('manifest.json','application.bin'):
            shutil.copyfile(source/name,stage/name)
            (stage/name).chmod(0o600)
        if read_package(stage,key)!=manifest:
            raise ValueError('复制后的固件包不完整')
        stage.rename(destination)
    return {'ok':True,'release_id':manifest['id'],'already_published':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--public-key',type=Path,default=Path(__file__).resolve().parents[1]/'ota-public.pem')
    args=parser.parse_args()
    print(json.dumps(publish(args.source,args.target,args.public_key),ensure_ascii=False))


if __name__=='__main__': main()
