import json

from PIL import Image, ImageDraw

row = json.loads(open("/tmp/m1_fixed/manifest.jsonl").readline())
b = Image.open("/tmp/m1_fixed/" + row["files"]["before"])
a = Image.open("/tmp/m1_fixed/" + row["files"]["after"])
sheet = Image.new("RGB", (b.width * 2 + 12, b.height + 34), (250, 248, 245))
d = ImageDraw.Draw(sheet)
d.text((4, 4), f"{row['sku_id']} {row['placement']} {row['camera_kind']} — rev.7 "
              f"nipple holdout   before | after", fill=(40, 35, 30))
sheet.paste(b, (4, 28))
sheet.paste(a, (b.width + 8, 28))
sheet.save("/mnt/agents/output/rev7_nipple_fix.png")
print("saved", row["pair_id"])
