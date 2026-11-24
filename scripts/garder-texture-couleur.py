# Retire d'un modèle .glb ce qu'AURORA ne lit pas (11/11/2026, les formes du noyau données par ID Studio IA).
#
# Les modèles faits avec Meshy portent trois textures : la couleur, le métal et le relief. Le lecteur d'AURORA
# (frontend/src/components/hologram/glb.ts) ne lit que la couleur : les deux autres pèsent pour rien (5 Mo sur 11
# pour la sphère « Cosmique »). Ce script recopie le maillage tel quel et ne garde que la texture de couleur,
# réduite à COTE points de côté au plus et réencodée en JPEG.
#
# Il vient APRÈS la réduction du maillage, faite avec Blender par le script d'Athéna (celui qui a allégé les
# modèles du globe, cité dans frontend/public/terre/SOURCE.txt) :
#   blender -b --factory-startup -P <Athéna>/scripts/alleger-modele-globe.py -- SOURCE.glb REDUIT.glb TRIANGLES 4096 APERCUS
#   python -I scripts/garder-texture-couleur.py REDUIT.glb SORTIE.glb [COTE=2048] [QUALITE=88]
# Il lui faut Pillow. Le fichier source n'est jamais modifié ; la sortie n'écrase rien (refus si elle existe).
import io
import json
import os
import struct
import sys

from PIL import Image

source, sortie = sys.argv[1], sys.argv[2]
cote = int(sys.argv[3]) if len(sys.argv) > 3 else 2048
qualite = int(sys.argv[4]) if len(sys.argv) > 4 else 88
if os.path.exists(sortie):
    sys.exit(f'{sortie} existe déjà : rien n’a été écrit.')

with open(source, 'rb') as f:
    magie, version, _ = struct.unpack('<4sII', f.read(12))
    long_json, genre = struct.unpack('<II', f.read(8))
    if magie != b'glTF' or genre != 0x4E4F534A:
        sys.exit('Ce fichier n’est pas un .glb lisible.')
    doc = json.loads(f.read(long_json))
    long_bin, genre = struct.unpack('<II', f.read(8))
    if genre != 0x004E4942:
        sys.exit('Ce .glb renvoie à d’autres fichiers : il n’est pas traité.')
    donnees = f.read(long_bin)

vues = doc['bufferViews']
couleur = doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture']['index']
image = doc['images'][doc['textures'][couleur]['source']]
vue = vues[image['bufferView']]
photo = Image.open(io.BytesIO(donnees[vue.get('byteOffset', 0):vue.get('byteOffset', 0) + vue['byteLength']])).convert('RGB')
avant = photo.size
if max(photo.size) > cote:
    photo.thumbnail((cote, cote), Image.LANCZOS)
tampon = io.BytesIO()
photo.save(tampon, 'JPEG', quality=qualite, optimize=True)

# Les vues du maillage, recopiées dans l'ordre et alignées sur 4 octets ; puis la seule image gardée.
des_images = {i['bufferView'] for i in doc.get('images', []) if 'bufferView' in i}
sortie_bin, nouvelles, rang = bytearray(), [], {}
for i, v in enumerate(vues):
    if i in des_images:
        continue
    sortie_bin += b'\0' * (-len(sortie_bin) % 4)
    rang[i] = len(nouvelles)
    nouvelles.append({**v, 'buffer': 0, 'byteOffset': len(sortie_bin)})
    sortie_bin += donnees[v.get('byteOffset', 0):v.get('byteOffset', 0) + v['byteLength']]
for a in doc['accessors']:
    a['bufferView'] = rang[a['bufferView']]
sortie_bin += b'\0' * (-len(sortie_bin) % 4)
nouvelles.append({'buffer': 0, 'byteOffset': len(sortie_bin), 'byteLength': tampon.tell()})
sortie_bin += tampon.getvalue()
sortie_bin += b'\0' * (-len(sortie_bin) % 4)

doc['bufferViews'] = nouvelles
doc['images'] = [{'bufferView': len(nouvelles) - 1, 'mimeType': 'image/jpeg'}]
doc['textures'] = [{'source': 0}]
doc.pop('samplers', None)
doc['materials'] = [{'doubleSided': True, 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}]
doc['buffers'] = [{'byteLength': len(sortie_bin)}]
texte = json.dumps(doc, separators=(',', ':')).encode()
texte += b' ' * (-len(texte) % 4)
with open(sortie, 'xb') as f:
    f.write(struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(texte) + 8 + len(sortie_bin)))
    f.write(struct.pack('<II', len(texte), 0x4E4F534A) + texte)
    f.write(struct.pack('<II', len(sortie_bin), 0x004E4942) + sortie_bin)

primitive = doc['meshes'][0]['primitives'][0]
print(f'{os.path.basename(sortie)} : {doc["accessors"][primitive["indices"]]["count"] // 3} triangles, '
      f'texture {avant[0]} x {avant[1]} -> {photo.size[0]} x {photo.size[1]} ({tampon.tell() / 1e6:.2f} Mo), '
      f'{os.path.getsize(source) / 1e6:.1f} Mo -> {os.path.getsize(sortie) / 1e6:.1f} Mo')
