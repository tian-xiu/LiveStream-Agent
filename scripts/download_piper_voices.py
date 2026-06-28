"""Download piper-tts Chinese voice models from hf-mirror.com."""
import ssl
import json
import shutil
from pathlib import Path
from urllib.request import urlopen, Request

# SSL monkey-patch for Windows cert store issues
original = ssl.SSLContext.load_verify_locations
def patched(self, *args, **kwargs):
    try: return original(self, *args, **kwargs)
    except ssl.SSLError: pass
ssl.SSLContext.load_verify_locations = patched

MIRROR = "https://hf-mirror.com"
VOICES_JSON = f"{MIRROR}/rhasspy/piper-voices/resolve/main/voices.json?download=true"
URL_FMT = f"{MIRROR}/rhasspy/piper-voices/resolve/main/{{lang_family}}/{{lang_code}}/{{voice_name}}/{{voice_quality}}/{{lang_code}}-{{voice_name}}-{{voice_quality}}{{ext}}?download=true"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

def fetch(url):
    req = Request(url, headers={"User-Agent": UA})
    return urlopen(req, timeout=60)

def main():
    output_dir = Path(r"F:\project\python\LiveStream-Agent\data\piper_voices")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: fetch voices list
    print("Fetching voices.json...")
    with fetch(VOICES_JSON) as r:
        voices = json.load(r)
    print(f"Total voices: {len(voices)}")
    
    # Step 2: find Chinese voices
    chinese = {k: v for k, v in voices.items() if k.startswith("zh_CN")}
    print(f"Chinese voices ({len(chinese)}):")
    for name in sorted(chinese):
        info = chinese[name]
        print(f"  {name}  (quality: {info.get('quality', '?')}, size: {info.get('files', {}).get('model', {}).get('size_bytes', '?')})")
    
    # Step 3: download each Chinese voice
    for voice_name in sorted(chinese):
        # voice_name format: "zh_CN-chaowen-medium"
        parts = voice_name.split("-")
        lang_full = parts[0]      # zh_CN
        voice = parts[1]          # chaowen
        quality = parts[2]        # medium
        lang_family = lang_full.split("_")[0]  # zh
        
        onnx_file = output_dir / f"{voice_name}.onnx"
        config_file = output_dir / f"{voice_name}.onnx.json"
        
        fmt = {
            "lang_family": lang_family,
            "lang_code": lang_full,
            "voice_name": voice,
            "voice_quality": quality,
        }
        
        for ext, path in [(".onnx", onnx_file), (".onnx.json", config_file)]:
            if path.exists() and path.stat().st_size > 0:
                print(f"  [skip] {path.name} exists ({path.stat().st_size} bytes)")
                continue
            url = URL_FMT.format(ext=ext, **fmt)
            print(f"  Downloading {path.name} from {url}...")
            with fetch(url) as resp:
                with open(path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            print(f"  -> Saved {path.name} ({path.stat().st_size} bytes)")
    
    print(f"\nAll done! Files in: {output_dir}")

if __name__ == "__main__":
    main()
