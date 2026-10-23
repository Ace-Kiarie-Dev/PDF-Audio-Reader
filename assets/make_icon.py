"""Regenerates assets/readout.ico and readout.png (run from the project root)."""

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((32, 32, S - 32, S - 32), radius=230, fill=(20, 22, 28, 255))
d.ellipse((222, 160, 802, 740), outline=(139, 124, 255, 255), width=54)
d.polygon([(450, 310), (450, 590), (684, 450)], fill=(139, 124, 255, 255))
d.rounded_rectangle((292, 830, 732, 874), radius=22, fill=(255, 212, 90, 255))  # highlighter stroke
img.save("assets/readout.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save("assets/readout.png")
