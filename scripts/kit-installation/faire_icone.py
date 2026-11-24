"""AURORA's icon from the picture ID Studio IA gave (22/11/2026: « Ici, votre AURORA change icone du bureau »): the sphere
cut out round, on a transparent ground, as a PNG for the installation window and as a Windows .ico of every size.
Needs Pillow, which AURORA's own environment does not have: run it with another Python that has it.
Usage: python -I -X utf8 faire_icone.py <image> <dossier assets> [centre_x centre_y rayon]"""
import sys

from PIL import Image, ImageDraw, ImageFilter

source, assets = sys.argv[1], sys.argv[2]
# Where the sphere is in the picture of 22/11/2026 (1536 x 1024): read on the picture, not guessed by the code.
cx, cy, rayon = (int(v) for v in sys.argv[3:6]) if len(sys.argv) >= 6 else (770, 425, 400)

image = Image.open(source).convert('RGBA')
bord = rayon + 12                                    # a little room for the glow around the sphere
carre = image.crop((cx - bord, cy - bord, cx + bord, cy + bord))
masque = Image.new('L', carre.size, 0)
ImageDraw.Draw(masque).ellipse((12, 12, carre.width - 12, carre.height - 12), fill=255)
carre.putalpha(masque.filter(ImageFilter.GaussianBlur(7)))   # a soft edge: the glow fades instead of being cut

grand = carre.resize((512, 512), Image.LANCZOS)
grand.save(f'{assets}/aurora-sphere.png')
grand.save(f'{assets}/aurora.ico', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
print('écrits :', f'{assets}/aurora-sphere.png', grand.size, 'et', f'{assets}/aurora.ico')
