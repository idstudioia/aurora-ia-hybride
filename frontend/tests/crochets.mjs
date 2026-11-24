// Crochets de chargement des tests de la carte des outils (enregistrés par tests/aide-carte.mjs ; Node les exécute
// dans un fil à part). Ils permettent de charger les fichiers de src dans Node, sans Vite ni navigateur :
// - un import sans extension (« ./model ») trouve son fichier .ts ou .tsx ;
// - un fichier .tsx est traduit par TypeScript (les .ts sont lus par Node lui-même, --experimental-strip-types) ;
// - une feuille de style importée ne pèse rien ;
// - lib/store et lib/stream (qui parlent au service local) sont remplacés par des bouchons : aucun appel réseau.
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const BOUCHONS = [
  [/(^|\/)lib\/store$/, new URL('./bouchons/store.mjs', import.meta.url).href],
  [/(^|\/)lib\/stream$/, new URL('./bouchons/stream.mjs', import.meta.url).href],
];
const INTROUVABLE = 'ERR_MODULE_NOT_FOUND';

export async function resolve(specifier, context, nextResolve) {
  if (!/^\.{1,2}\//.test(specifier)) return nextResolve(specifier, context);
  if (specifier.endsWith('.css')) return { url: 'data:text/javascript,export default {};', shortCircuit: true };
  for (const [motif, url] of BOUCHONS) if (motif.test(specifier)) return { url, shortCircuit: true };
  try { return await nextResolve(specifier, context); }
  catch (err) {
    if (err?.code !== INTROUVABLE || /\.(m?[jt]sx?|json)$/.test(specifier)) throw err;
    try { return await nextResolve(`${specifier}.ts`, context); }
    catch (err2) {
      if (err2?.code !== INTROUVABLE) throw err2;
      return nextResolve(`${specifier}.tsx`, context);
    }
  }
}

export async function load(url, context, nextLoad) {
  if (!url.endsWith('.tsx')) return nextLoad(url, context);
  const fichier = fileURLToPath(url);
  const source = await readFile(fichier, 'utf8');
  const sortie = ts.transpileModule(source, {
    fileName: fichier,
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return { format: 'module', source: sortie.outputText, shortCircuit: true };
}
