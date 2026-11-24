// Aides des tests qui lisent un fichier GLB (tests/glb.test.mjs, tests/origine.test.mjs) : les fichiers sont fabriqués
// ici, en mémoire. Aucun vrai modèle n'est lu, et le dossier d'AURORA (~/.aurora) n'est jamais touché.

/** Un fichier GLB en mémoire : en-tête, bloc JSON, et bloc binaire s'il y en a un. */
export function glb(json, bin) {
  const j = new TextEncoder().encode(JSON.stringify(json));
  const jp = new Uint8Array(j.length + (4 - j.length % 4) % 4).fill(0x20);
  jp.set(j);
  const total = 20 + jp.length + (bin ? 8 + bin.byteLength : 0);
  const out = new Uint8Array(total), dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546c67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
  dv.setUint32(12, jp.length, true); dv.setUint32(16, 0x4e4f534a, true); out.set(jp, 20);
  if (bin) {
    const o = 20 + jp.length;
    dv.setUint32(o, bin.byteLength, true); dv.setUint32(o + 4, 0x004e4942, true); out.set(new Uint8Array(bin), o + 8);
  }
  return out.buffer;
}

const ECRIRE = { f32: [4, 'setFloat32'], u16: [2, 'setUint16'], u8: [1, 'setUint8'] };

/** Des suites de nombres rangées bout à bout dans un bloc binaire, chacune alignée sur 4 octets. `parts` : des paires
 *  [type, nombres], le type parmi f32, u16, u8. Rend le bloc et, pour chaque suite, sa place (un « bufferView »). */
export function tampon(parts) {
  const vues = [];
  let fin = 0;
  for (const [type, nombres] of parts) {
    const byteLength = nombres.length * ECRIRE[type][0];
    vues.push({ buffer: 0, byteOffset: fin, byteLength });
    fin += byteLength + (4 - byteLength % 4) % 4;
  }
  const bin = new ArrayBuffer(fin), dv = new DataView(bin);
  parts.forEach(([type, nombres], i) => {
    const [taille, ecrire] = ECRIRE[type];
    nombres.forEach((v, k) => dv[ecrire](vues[i].byteOffset + k * taille, v, true));
  });
  return { bin, vues };
}
