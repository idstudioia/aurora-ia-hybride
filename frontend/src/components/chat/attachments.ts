// Reading files the user attaches to a message. Small text files are read here; PDF and Word files (E4) are read
// by the local service, which sends back their text; an image, or the screen captured on a click (E2), is described
// on this PC by a local vision model, and that description is what is joined. Either way the content travels as
// data (AskInput.attachments), never as instructions.
import { api } from '../../lib/api';
import { fmtMs, fmtTime } from '../../lib/format';
import type { DocumentText, VisionReading } from '../../lib/types';
import { addAttachment, MAX_ATTACHMENTS, type PendingAttachment, type Photo3dChoice } from './prefs';
import { fmtBytes, fmtNum } from './text';

export const TEXT_EXTENSIONS = ['.txt', '.md', '.csv', '.tsv', '.json', '.log'];
export const DOC_EXTENSIONS = ['.pdf', '.docx'];
export const MAX_ATTACHMENT_BYTES = 200 * 1024;
export const MAX_DOCUMENT_BYTES = 15 * 1024 * 1024;
/** A 3D model the user already has (10/11/2026). It is not joined to the message: the local service keeps it
 *  (tools/lab.py import_model, same bound) and the laboratory of the home page shows it. */
export const MODEL_EXTENSIONS = ['.stl', '.glb', '.obj'];
export const MAX_MODEL_BYTES = 64 * 1024 * 1024;
export const ACCEPT_ATTR = [...TEXT_EXTENSIONS, ...DOC_EXTENSIONS, '.doc', 'image/*', ...MODEL_EXTENSIONS].join(',');
export const isDocument = (name: string) => DOC_EXTENSIONS.some(ext => name.toLowerCase().endsWith(ext));
export const isModel3d = (name: string) => MODEL_EXTENSIONS.some(ext => name.toLowerCase().endsWith(ext));

let seq = 0;
const newId = () => `att${Date.now().toString(36)}${(seq++).toString(36)}`;

export type ReadOutcome = { ok: true; attachment: PendingAttachment; notes?: string[] } | { ok: false; message: string };
/** 23/11/2026: what a long reading says while it runs (`progress`), and how the person stops it (`signal`). Used by
 *  a PDF made of images, read page by page. */
export type ReadWatch = { progress?: (message: string) => void; signal?: AbortSignal };

/** A PDF with no text at all (scanned, or printed as drawings; 23/11/2026, ID Studio IA's article printed by « Microsoft: Print
 *  To PDF »): the local service draws each page as an image and the local vision model copies it on this PC, 20 to
 *  40 s a page. The page being read is said while it runs, repeated so that the notice stays; the person can stop,
 *  and the pages already read are then joined. What is joined is a model's reading: its first line says so. */
async function readImagePages(file: File, name: string, doc: DocumentText, watch?: ReadWatch): Promise<ReadOutcome> {
  const { model, max_pages, max_chars } = doc.images!;
  const total = doc.pages ?? 0;
  const last = Math.min(total, max_pages);
  if (last < 1) return { ok: false, message: `« ${name} » n’a pas été joint : ce PDF n’a aucune page.` };
  const head = `Texte de « ${name} », un PDF fait d’images : chaque page a été recopiée sur ce PC par le modèle de vision local (${model}). `
    + 'C’est la lecture d’un modèle, pas le texte d’origine : elle peut contenir des erreurs de mots ou de chiffres.';
  const start = Date.now();
  let page = 1;
  const say = () => {
    const ms = Date.now() - start;
    watch?.progress?.(`« ${name} » est fait d’images : lecture de la page ${page} sur ${last} par le modèle de vision local, sur ce PC `
      + `(${ms < 60_000 ? `${Math.round(ms / 1000)} s` : fmtMs(ms)} ; 20 à 40 s par page). Pour arrêter : le carré à la place du trombone, les pages déjà lues seront jointes.`);
  };
  const tick = window.setInterval(say, 4000);   // a notice fades after a few seconds: this one stays, with the time
  const blocks: string[] = [];
  const unread: number[] = [];
  let chars = head.length, cut = 0, failures = 0, stopped = false, full = false, why = '';
  try {
    for (; page <= last; page++) {
      if (watch?.signal?.aborted) { stopped = true; break; }
      say();
      try {
        const read = await api.readDocumentPage(file, page, watch?.signal);
        const block = `[Page ${page}]\n${read.text.trim()}`;
        if (chars + block.length + 2 > max_chars) { full = true; break; }
        blocks.push(block);
        chars += block.length + 2;
        if (read.truncated) cut++;
        failures = 0;
      } catch (err) {
        if (watch?.signal?.aborted) { stopped = true; break; }
        why = err instanceof Error ? err.message : 'lecture impossible';
        unread.push(page);
        // the first page fails, or two in a row: the PDF, the model or the service is the cause, not one page
        if (!blocks.length || ++failures >= 2) break;
      }
    }
  } finally {
    window.clearInterval(tick);
  }
  if (!blocks.length) {
    return { ok: false, message: `« ${name} » n’a pas été joint : ${stopped ? 'lecture arrêtée avant la fin de la première page.' : why || 'aucune page n’a pu être lue.'}` };
  }
  const notes = [`« ${name} » : PDF fait d’images, ${blocks.length} page(s) sur ${total} recopiée(s) sur ce PC par ${model} en ${fmtMs(Date.now() - start)}. `
    + 'C’est la lecture d’un modèle : elle peut contenir des erreurs de mots ou de chiffres.'];
  if (stopped) notes.push(`« ${name} » : lecture arrêtée à votre demande à la page ${page} ; les pages lues sont jointes.`);
  else if (full) notes.push(`« ${name} » : lecture arrêtée à la page ${page}, la pièce jointe a atteint sa taille maximale (${max_chars.toLocaleString('fr-FR')} caractères).`);
  else if (total > last && page > last) notes.push(`« ${name} » : seules les ${last} premières pages sur ${total} ont été lues.`);
  if (unread.length) notes.push(`« ${name} » : page(s) non lue(s) : ${unread.join(', ')} (${why})${page <= last && !stopped && !full ? ' ; la lecture s’est arrêtée là' : ''}.`);
  if (cut) notes.push(`« ${name} » : ${cut} page(s) coupée(s), le modèle a atteint sa longueur maximale.`);
  return { ok: true, attachment: { id: newId(), name, text: `${head}\n\n${blocks.join('\n\n')}`, size: file.size }, notes };
}

/** A PDF or a Word file: its text comes from the local service; what was read (pages, a cut) is said. */
async function readDocument(file: File, name: string, watch?: ReadWatch): Promise<ReadOutcome> {
  if (file.size > MAX_DOCUMENT_BYTES) return { ok: false, message: `« ${name} » dépasse 15 Mo (${fmtBytes(file.size)}).` };
  try {
    const doc = await api.extractDocument(file, name);
    if (doc.images) return await readImagePages(file, name, doc, watch);
    const what = doc.pages ? `${doc.pages_read ?? doc.pages} page(s) sur ${doc.pages}` : `${doc.chars.toLocaleString('fr-FR')} caractères`;
    return {
      ok: true,
      attachment: { id: newId(), name, text: doc.text, size: file.size },
      notes: [`« ${name} » lu par AURORA (${what}).`, ...doc.notes.map(n => `« ${name} » : ${n}`)],
    };
  } catch (err) {
    return { ok: false, message: `« ${name} » n’a pas été joint : ${err instanceof Error ? err.message : 'lecture impossible'}` };
  }
}

// ------------------------------------------------------------------ images (E2): read on this PC by a vision model

export const isImage = (file: File) => file.type.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|svg|heic)$/.test(file.name.toLowerCase());
// 22/11/2026: 1280 instead of 1600. Measured on a fake screen read by ornith-1.5:9b (RX 6750 XT): reading the image
// takes 16.9 s at 1600 points, 8.1 s at 1280 with every text found, 3.7 s at 1024 (Athéna's size) with two small texts
// lost. 1280 is also what a 4K screen capture comes to (backend tools/vision.py).
const IMAGE_SIDE = 1280;
const PREVIEW_SIDE = 360;    // the thumbnail shown to the person (never sent)

/** The image redrawn as a JPEG of `side` px at most: any format the browser shows (WebP, GIF, BMP…) becomes one the
 *  vision model reads (PNG or JPEG), and a photo of several megapixels travels as a few hundred kilobytes. */
async function asJpeg(image: Blob, side: number, quality: number): Promise<Blob> {
  const bitmap = await createImageBitmap(image);
  try {
    const scale = Math.min(1, side / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('dessin impossible');
    ctx.fillStyle = '#fff';                 // a transparent PNG would turn black in JPEG
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(b => (b ? resolve(b) : reject(new Error('conversion impossible'))), 'image/jpeg', quality));
  } finally {
    bitmap.close();
  }
}

const asDataUrl = (blob: Blob) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result));
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(blob);
});

const seenBy = (r: VisionReading) => `${r.model}, ${fmtNum(r.seconds)} s, ${r.processor}`;
const cutNote = (r: VisionReading) => (r.truncated ? '\n\n(Description coupée : le modèle a atteint sa longueur maximale.)' : '');

/** An image: what the local vision model sees on it becomes the attachment's text (data, never an instruction). */
async function readImage(file: File, name: string): Promise<ReadOutcome> {
  if (file.size > MAX_DOCUMENT_BYTES) return { ok: false, message: `« ${name} » dépasse 15 Mo (${fmtBytes(file.size)}).` };
  let jpeg: Blob, preview: string;
  try {
    jpeg = await asJpeg(file, IMAGE_SIDE, 0.9);
    preview = await asDataUrl(await asJpeg(file, PREVIEW_SIDE, 0.8));
  } catch {
    return { ok: false, message: `« ${name} » n’a pas été joint : le navigateur ne sait pas ouvrir cette image (PNG, JPEG, WebP, GIF ou BMP).` };
  }
  try {
    const seen = await api.describeImage(jpeg);
    return {
      ok: true,
      attachment: { id: newId(), name, size: file.size, preview,
        text: `Description de l’image « ${name} », faite sur ce PC par le modèle de vision local (${seenBy(seen)}) :\n\n${seen.text}${cutNote(seen)}` },
      notes: [`« ${name} » décrite sur ce PC par ${seen.model} en ${fmtNum(seen.seconds)} s : la description est jointe, l’image ne part nulle part.`],
    };
  } catch (err) {
    return { ok: false, message: `« ${name} » n’a pas été joint : ${err instanceof Error ? err.message : 'lecture impossible'}` };
  }
}

let screenRun: Promise<string[]> | null = null;

/** « Capture d’écran » button only: the screen is captured now, read on this PC, and joined as a description.
 *  A click on the other button (bar or chat) while a capture is read joins it instead of starting a second one. */
export function attachScreen(): Promise<string[]> {
  screenRun ??= readScreen().finally(() => { screenRun = null; });
  return screenRun;
}

async function readScreen(): Promise<string[]> {
  try {
    const seen = await api.describeScreen();
    const at = fmtTime(new Date().toISOString(), false);
    addAttachment({
      id: newId(), name: `Capture d’écran ${at}`, size: Math.round((seen.image.length - seen.image.indexOf(',') - 1) * 3 / 4),
      preview: seen.image,
      text: `Capture de l’écran principal (${seen.width}×${seen.height}), faite à ${at} sur demande, décrite sur ce PC par le `
        + `modèle de vision local (${seenBy(seen)}) :\n\n${seen.text}${cutNote(seen)}`,
    });
    return [`Écran capturé et lu sur ce PC par ${seen.model} en ${fmtNum(seen.seconds)} s. `
      + 'La description est jointe au prochain message (onglet Fichiers du chat pour la relire) ; rien n’est parti ailleurs.'];
  } catch (err) {
    return [`Capture d’écran non jointe : ${err instanceof Error ? err.message : 'lecture impossible'}`];
  }
}

export async function readAttachment(file: File, watch?: ReadWatch): Promise<ReadOutcome> {
  const name = file.name || 'fichier';
  const lower = name.toLowerCase();
  if (isImage(file)) return readImage(file, name);
  if (lower.endsWith('.doc')) {
    return { ok: false, message: `Ancien format Word (.doc) : ouvrez « ${name} » dans Word et enregistrez-le en .docx ou en PDF, puis joignez-le.` };
  }
  if (isDocument(lower)) return readDocument(file, name, watch);
  if (!TEXT_EXTENSIONS.some(ext => lower.endsWith(ext))) {
    return { ok: false, message: `Type non pris en charge : « ${name} ». Formats acceptés : PDF, Word (.docx), images, modèles 3D (${MODEL_EXTENSIONS.join(' ')}), ${TEXT_EXTENSIONS.join(' ')}.` };
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    return { ok: false, message: `« ${name} » dépasse 200 Ko (${fmtBytes(file.size)}).` };
  }
  let text: string;
  try {
    text = await file.text();
  } catch {
    return { ok: false, message: `Lecture impossible : « ${name} ».` };
  }
  if (text.includes('\u0000')) return { ok: false, message: `« ${name} » ne semble pas être un fichier texte.` };
  if (!text.trim()) return { ok: false, message: `« ${name} » est vide.` };
  return { ok: true, attachment: { id: newId(), name, text, size: file.size } };
}

/** A 3D model (STL, GLB, OBJ): given to the local service, which keeps it as a scene, then shown by `onScene`
 *  (the laboratory of the home page). Returns what to tell the person. Nothing leaves this PC. */
async function openModel(file: File, onScene?: (sceneId: string) => void): Promise<string> {
  const name = file.name || 'modèle';
  if (file.size > MAX_MODEL_BYTES) return `« ${name} » dépasse 64 Mo (${fmtBytes(file.size)}) : modèle 3D non ouvert.`;
  try {
    const kept = await api.labImportModel(file, name);
    onScene?.(kept.id);
    return `« ${name} » ${kept.deja ? 'était déjà gardé : il est rouvert' : 'est ouvert'} dans le laboratoire 3D de l’accueil, sous le nom « ${kept.titre} ». `
      + 'Il reste sur ce PC et n’est pas joint au message.';
  } catch (err) {
    return `« ${name} » : ${err instanceof Error ? err.message : 'modèle 3D non ouvert.'}`;
  }
}

const PHOTO_3D_SIDE = 1024;   // Pixal3D and TripoSR look at 512 px: more would only make the cut-out slower
/** The cut-out kept more than two thirds of the photo as « the object ». Measured on 15 images (10/11/2026): the 9
 *  that gave a good shape kept 4 to 62 %; 4 of the 6 failures (drawings, patent sheets) kept 71 to 86 %. It is said
 *  as a caution, never as a verdict: the two other failures kept 7 and 22 %. */
const PHOTO_3D_WIDE = 0.67;

/** « Photo vers 3D » (10/11/2026): the photo of an object is given to a local AI on this PC, which rebuilds its shape
 *  (`choix`: Pixal3D first, « detaille », or TripoSR first, « rapide »; the other one is the way out, and the service
 *  says which one did it); the shape then opens in the laboratory of the home page (`onScene`). The photo is not
 *  joined to the message and goes nowhere. Returns what to tell the person, with what the run measured. */
export async function photoTo3d(file: File, onScene?: (sceneId: string) => void, choix: Photo3dChoice = 'detaille'): Promise<string[]> {
  const name = file.name || 'photo';
  if (!isImage(file)) return [`« ${name} » n’est pas une image : choisissez la photo d’un objet (PNG, JPEG, WebP, GIF ou BMP).`];
  if (file.size > MAX_DOCUMENT_BYTES) return [`« ${name} » dépasse 15 Mo (${fmtBytes(file.size)}) : aucun modèle 3D n’a été fait.`];
  let jpeg: Blob;
  try {
    jpeg = await asJpeg(file, PHOTO_3D_SIDE, 0.92);
  } catch {
    return [`« ${name} » : le navigateur ne sait pas ouvrir cette image (PNG, JPEG, WebP, GIF ou BMP). Aucun modèle 3D n’a été fait.`];
  }
  try {
    const made = await api.labPhotoModel(jpeg, name, choix);
    onScene?.(made.id);
    const engine = made.moteur || 'TripoSR';
    const rough = engine === 'TripoSR';   // what the trials of 10/11/2026 measured on TripoSR, and on it alone
    const notes = [`« ${name} » : forme reconstruite sur ce PC par ${engine} en ${fmtNum(made.secondes)} s, ouverte dans le laboratoire 3D de l’accueil sous le nom « ${made.titre} ». `
      + `Les côtés que la photo ne montre pas sont inventés${rough ? ' ; un dessin au trait ou une photo chargée donne souvent une plaque ou un bloc informe' : ''}. `
      + 'La photo n’est partie nulle part et n’est pas jointe au message.'];
    if (made.repli) {
      const first = rough ? 'Pixal3D' : 'TripoSR';   // two engines: the one that did not make the shape was tried first
      notes.push(`${first} n’a pas pu faire cette forme : ${engine}, le moteur de secours, l’a faite. Ce que ${first} a dit : ${made.repli}`);
    }
    if (rough && (made.couverture ?? 0) > PHOTO_3D_WIDE) {
      notes.push(`Attention : le détourage a pris ${Math.round(made.couverture! * 100)} % de l’image pour l’objet. Aux essais, c’était le cas des dessins et des planches, qui donnent une plaque : une photo ou un rendu de l’objet seul, détaché du fond, donne mieux.`);
    }
    return notes;
  } catch (err) {
    return [`« ${name} » : ${err instanceof Error ? err.message : 'aucun modèle 3D n’a été fait.'}`];
  }
}

/** Read every file, queue the valid ones and return the messages to show for the others. A 3D model is not queued:
 *  it opens as a scene (`onScene`). `watch`: for a PDF made of images, read page by page (readImagePages). */
export async function attachFiles(files: FileList | File[] | null, onScene?: (sceneId: string) => void, watch?: ReadWatch): Promise<string[]> {
  const list = files ? Array.from(files) : [];
  const messages: string[] = [];
  for (const file of list.slice(0, MAX_ATTACHMENTS)) {
    if (isModel3d(file.name)) { messages.push(await openModel(file, onScene)); continue; }
    const res = await readAttachment(file, watch);
    if (res.ok) { addAttachment(res.attachment); messages.push(...(res.notes ?? [])); }
    else messages.push(res.message);
  }
  if (list.length > MAX_ATTACHMENTS) messages.push(`${MAX_ATTACHMENTS} fichiers au maximum par message.`);
  return messages;
}
