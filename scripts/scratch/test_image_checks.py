import hashlib
from pathlib import Path

def check_images(images_dir: Path, index_file: Path):
    # Load index to find allowed copies
    copies = {}
    if index_file.is_file():
        # Simple parsing of index to find explicit copies
        # Assuming format like "ready.png, 900x700_idle.png" or just checking for "copy of"
        pass
        
    hashes = {}
    for p in images_dir.glob("*.png"):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h in hashes:
            # Check if this is an allowed copy
            print(f"Byte-identical images found: {p.name} and {hashes[h]}")
            # Here I would check INDEX.txt for exceptions
        else:
            hashes[h] = p.name
            
    # Check images with different state names differ in content
    # (e.g. idle vs running)
    states = {}
    for p in images_dir.glob("*.png"):
        # Assuming names like size_state.png
        parts = p.name.split('_')
        if len(parts) >= 2:
            state = parts[-1]
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            if state in states and states[state] != h:
                # Same state, different content? Or different state, same content?
                # The prompt: "images whose names differ in state must differ in content"
                pass

    return True

# Test
images_dir = Path("scratch/images")
images_dir.mkdir(parents=True, exist_ok=True)
(images_dir / "a.png").write_bytes(b"content1")
(images_dir / "b.png").write_bytes(b"content1")
check_images(images_dir, Path("scratch/INDEX.txt"))
