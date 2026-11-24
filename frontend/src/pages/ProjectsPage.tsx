// Page « Projets » (13/11/2026). ID Studio IA : « chaque projet conserve ses fichiers techniques modifiables, pas uniquement
// son modèle 3D […] avant toute fabrication, les calculs et les circuits devront être vérifiés ».
// La liste vient de GET /api/projets, un projet de GET /api/projets/<id> (tools/projets.py). Une seule chose s'écrit,
// depuis le 23/11/2026 : un projet vide, par le bouton « Créer un projet » (POST /api/projets).
// L'état d'une exigence ou d'une vérification est celui que le projet déclare ; seule la présence des fichiers est
// mesurée par le service. La page le dit.
import './projets.css';
import { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { Calculator, CircleCheck, ClipboardList, FolderKanban, RefreshCw, TriangleAlert, Wrench } from 'lucide-react';
import type { ProjetCalcul } from '../lib/types';
import { fmtNum } from '../components/chat/text';
import type { ProjetFabrication } from '../lib/types';
import { FileImage, FolderPlus, Hammer } from 'lucide-react';
import { api } from '../lib/api';
import { useAurora } from '../lib/store';
import { useSend } from '../components/chat/useSend';
import type { OutilConception, ProjetControles, ProjetDetail, ProjetFichier, ProjetVerification, ProjetsListe } from '../lib/types';
import { Badge, EmptyState, Panel, Spinner, Unavailable } from '../components/ui';

// 15/11/2026 : la fiche technique interactive (ses cadres, son modèle 3D), chargée seulement pour un projet qui en a une.
const FicheInteractive = lazy(() => import('./FicheInteractive'));

type Ton = 'green' | 'amber' | 'red' | 'cyan' | 'default';
const EXIGENCE: Record<string, [string, Ton]> = {
  tenue: ['tenue', 'green'], a_verifier: ['à vérifier', 'amber'], a_etudier: ['à étudier', 'cyan'], non_tenue: ['non tenue', 'red'],
};
const VERIFICATION: Record<string, [string, Ton]> = {
  faite: ['faite', 'green'], a_faire: ['à faire', 'amber'], bloquante: ['bloquante', 'red'],
};

const taille = (octets: number | null): string =>
  octets === null ? '' : octets >= 1e6 ? `${(octets / 1e6).toFixed(1).replace('.', ',')} Mo` : `${Math.max(1, Math.round(octets / 1000))} ko`;

function EtatFichier({ f }: { f: ProjetFichier }) {
  if (f.present) return <Badge tone="green" title={taille(f.taille)}>présent</Badge>;
  return f.genere
    ? <Badge tone="cyan" title="Ce fichier est fabriqué par un script du projet : il se refait.">à refaire</Badge>
    : <Badge tone="red" title="Ce fichier devrait être dans le dossier du projet.">manquant</Badge>;
}

// 13/11/2026, ID Studio IA : « FreeCAD et KiCad d'abord ». Le logiciel de la liste autorisée qui ouvre ce fichier, s'il y en a un.
// 15/11/2026 (« Oui, branche-les ») : la même recherche sert pour le moteur qui vérifie ce fichier, s'il est là.
const outilPour = (f: ProjetFichier, outils: OutilConception[], pour: 'ouvre' | 'verifie' = 'ouvre'): OutilConception | undefined =>
  f.present ? outils.find(o => o.autorise && (pour === 'ouvre' || o.moteur) && o[pour]?.some(ext => f.chemin.toLowerCase().endsWith(ext))) : undefined;

/** « Vérifier » : le moteur sans fenêtre du logiciel autorisé lit une copie du fichier et dit ce qu'il trouve
 *  (tools/moteurs_cao.py). Les chiffres et les violations sont les siens. Le service garde le dernier résultat de chaque
 *  fichier, avec son empreinte : c'est ce qui fait l'état d'une vérification « calculée » du projet. */
function VerifierFichier({ projet, f, outil, apres }: { projet: string; f: ProjetFichier; outil: OutilConception; apres?: () => void }) {
  const [recu, setRecu] = useState<ProjetVerification | null>(null);
  const [erreur, setErreur] = useState('');
  const [enCours, setEnCours] = useState(false);
  const verifier = async () => {
    setEnCours(true); setErreur('');
    // Le service garde ce résultat : `apres` relit le projet, dont une vérification calculée peut avoir changé d'état.
    try { setRecu(await api.projetVerifier(projet, f.chemin)); apres?.(); }
    catch (err) { setRecu(null); setErreur(err instanceof Error ? err.message : 'Service indisponible'); }
    finally { setEnCours(false); }
  };
  const quand = recu ? new Date(recu.date).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) : '';
  return (
    <>
      <button className="btn btn-sm prj-ouvrir" onClick={() => void verifier()} disabled={enCours}
        title={`Le moteur de ${outil.nom} lit une copie du fichier, sans ouvrir de fenêtre. Le fichier du projet n’est pas modifié.`}>
        {enCours && <Spinner />} {enCours ? 'Vérification…' : recu ? 'Revérifier' : 'Vérifier'}
      </button>
      {erreur && <span className="prj-verif" role="alert"><Badge tone="red">pas vérifié</Badge><strong>{erreur}</strong></span>}
      {recu && (
        <span className="prj-verif" role="status">
          <Badge tone={recu.verdict === 'conforme' ? 'green' : 'amber'}>{recu.verdict === 'conforme' ? 'conforme' : 'à revoir'}</Badge>
          <strong>{recu.resume}</strong>
          {(recu.objets?.length ?? 0) > 1 && recu.objets?.map(o => (
            <span className="prj-verif-ligne" key={o.nom}>
              {o.nom} <em>· {o.solides} solide{o.solides > 1 ? 's' : ''}, {o.valide ? 'valide' : 'non valide'}, {o.ferme ? 'fermé' : 'non fermé'} · {o.boite_mm.map(v => fmtNum(v, 1)).join(' × ')} mm</em>
            </span>
          ))}
          {/* Cinq lignes au plus (le service met les erreurs d'abord) : la suite se lit dans le logiciel. */}
          {recu.violations?.slice(0, 5).map((v, i) => (
            <span className="prj-verif-ligne" key={`${i}-${v.texte}`}>
              <Badge tone={v.gravite === 'erreur' ? 'red' : 'amber'}>{v.gravite}</Badge> {v.texte}{v.ou && <em> · {v.ou}</em>}
            </span>
          ))}
          {(recu.violations?.length ?? 0) + (recu.autres ?? 0) > 5 && (
            <span className="prj-verif-ligne"><em>… et {(recu.violations?.length ?? 0) + (recu.autres ?? 0) - 5} de plus, à lire dans {outil.nom}.</em></span>
          )}
          <small>{recu.moteur}, à {quand}, sur une copie. {recu.limite}</small>
        </span>
      )}
    </>
  );
}

/** FreeCAD et KiCad : reliés (dans Applications autorisées), à autoriser (vus sur ce PC), ou non trouvés. */
function Logiciels({ outils, versParametres }: { outils: OutilConception[]; versParametres: () => void }) {
  return (
    <Panel title="Logiciels de conception" subtitle="Ceux qui ouvrent les fichiers d’un projet" icon={<Wrench size={18} />}>
      <ul className="prj-outils">
        {outils.map(o => (
          <li key={o.id}>
            {o.autorise ? <Badge tone="green">relié</Badge> : o.sur_ce_pc.length ? <Badge tone="amber">à autoriser</Badge> : <Badge>non trouvé</Badge>}
            <span>
              <strong>{o.autorise ?? o.nom}</strong>
              <small>{o.autorise
                ? `Ouvre les fichiers ${o.ouvre.join(', ')} d’un projet : le bouton à côté du fichier, ou la phrase « ouvre … dans ${o.nom} ».${
                  o.verifie === undefined ? '' : o.moteur
                    ? ` Son moteur vérifie aussi les fichiers ${o.verifie.join(', ')} sans ouvrir de fenêtre : le bouton « Vérifier ».`
                    : ' Son moteur de vérification n’a pas été trouvé à côté du logiciel : pas de bouton « Vérifier ».'}`
                : o.sur_ce_pc.length
                  ? `Vu sur ce PC : ${o.sur_ce_pc.map(n => `« ${n} »`).join(', ')}. Ajoutez-le à vos applications autorisées pour qu’AURORA puisse lui donner un fichier.`
                  : 'Aucun raccourci trouvé sur le Bureau ni dans le menu Démarrer.'}</small>
            </span>
            {!o.autorise && o.sur_ce_pc.length > 0 && <button className="btn btn-sm" onClick={versParametres}>Paramètres</button>}
          </li>
        ))}
      </ul>
      <p className="prj-note">AURORA donne seulement le fichier au logiciel : elle ne le modifie pas, et ce que vous y changez se fait dans le logiciel.{outils.some(o => o.moteur) && ' Une vérification se fait sur une copie : le fichier du projet n’est pas modifié.'}</p>
    </Panel>
  );
}

const nombreSaisi = (texte: string): number => Number(texte.trim().replace(',', '.'));

/** « Les calculs en premier » (ID Studio IA, 13/11/2026) : une masse, une autonomie, un nombre d'articulations, confrontés au
 *  programme de calcul du projet. Tous les chiffres viennent de ce programme, jamais d'une IA. */
function Calcul({ projet }: { projet: ProjetDetail }) {
  const [masse, setMasse] = useState('5');
  const [autonomie, setAutonomie] = useState('120');
  const [articulations, setArticulations] = useState('12');
  const [resultat, setResultat] = useState<ProjetCalcul | null>(null);
  const [erreur, setErreur] = useState('');
  const [enCours, setEnCours] = useState(false);
  const calculer = async () => {
    const [m, a, n] = [nombreSaisi(masse), nombreSaisi(autonomie), nombreSaisi(articulations)];
    if (!(m >= 0.5 && m <= 50)) { setErreur('La masse va de 0,5 à 50 kg.'); return; }
    if (!(a >= 1 && a <= 1440)) { setErreur('L’autonomie va de 1 minute à 1440 minutes (24 heures).'); return; }
    if (!(Number.isInteger(n) && n >= 1 && n <= 64)) { setErreur('Le nombre d’articulations est un entier de 1 à 64.'); return; }
    setEnCours(true); setErreur('');
    try { setResultat(await api.projetCalcul(projet.id, m, a, n)); }
    catch (err) { setResultat(null); setErreur(err instanceof Error ? err.message : 'Calcul indisponible'); }
    finally { setEnCours(false); }
  };
  return (
    <Panel title="Calculs à partir d’un cahier des charges" subtitle={`Par le programme ${projet.calcul} du projet`} icon={<Calculator size={18} />}>
      <div className="prj-calcul">
        <form className="prj-demande" onSubmit={ev => { ev.preventDefault(); void calculer(); }}>
          <label>Masse du robot (kg)<input value={masse} onChange={ev => setMasse(ev.target.value)} inputMode="decimal" /></label>
          <label>Autonomie (minutes)<input value={autonomie} onChange={ev => setAutonomie(ev.target.value)} inputMode="decimal" /></label>
          <label>Articulations<input value={articulations} onChange={ev => setArticulations(ev.target.value)} inputMode="numeric" /></label>
          <button className="btn" type="submit" disabled={enCours}>{enCours ? <Spinner /> : <Calculator size={14} />} Calculer</button>
        </form>
        <p className="prj-note">Les chiffres viennent du programme de calcul du projet, jamais d’une IA. Ce sont des calculs sur des cotes de conception : rien n’a été fabriqué ni mesuré.</p>
        {erreur && <Unavailable what="Le calcul" reason={erreur} />}
        {resultat && (
          <>
            <div className={`prj-verdict ${resultat.verdict === 'faisable' ? 'ok' : ''}`} role="status">
              {resultat.verdict === 'faisable' ? <CircleCheck size={20} /> : <TriangleAlert size={20} />}
              <div>
                <strong>{resultat.resume ?? resultat.verdict}</strong>
                {resultat.pieces && <span>Servomoteur : {resultat.pieces.servomoteur}. Batterie : {resultat.pieces.batterie}.</span>}
              </div>
            </div>
            <table className="prj-table"><tbody>
              {resultat.lignes.map(l => (
                <tr key={l.quoi}>
                  <td><Badge tone={l.etat === 'tenue' ? 'green' : 'red'}>{l.etat === 'tenue' ? 'tenu' : 'non tenu'}</Badge></td>
                  <td>{l.quoi}<small>{l.detail}</small></td>
                </tr>
              ))}
            </tbody></table>
            {resultat.couples && (
              <table className="prj-table chiffres">
                <thead><tr><th>Couples aux articulations</th><th>Genou</th><th>Hanche</th><th>Épaule</th><th>Part du nominal</th></tr></thead>
                <tbody>
                  {resultat.couples.map(c => (
                    <tr key={c.situation}><td>{c.nom}</td><td>{fmtNum(c.genou_nm, 2)} N·m</td><td>{fmtNum(c.hanche_nm, 2)} N·m</td><td>{fmtNum(c.epaule_nm, 2)} N·m</td>
                      <td><Badge tone={c.part_nominal_pct <= 100 ? 'green' : 'red'}>{fmtNum(c.part_nominal_pct, 0)} %</Badge></td></tr>
                  ))}
                </tbody>
              </table>
            )}
            {resultat.masse && (
              <details>
                <summary>Masse poste par poste : {fmtNum(resultat.masse.total_kg, 2)} kg</summary>
                <table className="prj-table chiffres"><tbody>
                  {resultat.masse.postes.map(p => <tr key={p.nom}><td>{p.nom}</td><td>{fmtNum(p.grammes, 0)} g</td><td><small>{p.source}</small></td></tr>)}
                </tbody></table>
              </details>
            )}
            {resultat.hypotheses && resultat.hypotheses.length > 0 && (
              <details open>
                <summary>Ce que ce calcul suppose</summary>
                <ul>{resultat.hypotheses.map(h => <li key={h}>{h}</li>)}</ul>
              </details>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}

/** L'atelier (ID Studio IA, 15/11/2026 : « une ligne de progression, style animation, on voit que ça travaille petit à petit
 *  jusqu'à arriver à la finalité du robot. Par AURORA »). Le bouton lance le programme de fabrication du projet ; la
 *  ligne suit les étapes qu'il dit. Elle avance d'un cran à la fois à l'écran, jamais plus vite que le programme, et
 *  n'affiche « construit » que lorsqu'il a vraiment fini ; sa vraie durée est dite à la fin. */
function Fabrication({ projet, relire }: { projet: ProjetDetail; relire: () => void }) {
  const [etat, setEtat] = useState<ProjetFabrication | null>(null);
  const [montre, setMontre] = useState(0);          // le cran affiché, qui rattrape celui du programme
  const [erreur, setErreur] = useState('');
  const [garde, setGarde] = useState('');
  const [attente, setAttente] = useState(false);
  useEffect(() => {
    let actif = true;
    api.projetFabrication(projet.id).then(e => { if (actif) { setEtat(e); setMontre(e.etat === 'fini' ? e.sur ?? 0 : e.n ?? 0); } })
      .catch(() => { if (actif) setEtat(null); });
    return () => { actif = false; };
  }, [projet.id]);
  const enCours = etat?.etat === 'en_cours';
  useEffect(() => {                                 // tant que le programme tourne : relire son état deux fois par seconde
    if (!enCours) return;
    const t = window.setTimeout(() => { api.projetFabrication(projet.id).then(setEtat).catch(() => undefined); }, 500);
    return () => window.clearTimeout(t);
  }, [enCours, etat, projet.id]);
  const cible = etat?.etat === 'fini' ? etat.sur ?? 0 : etat?.n ?? 0;
  useEffect(() => {                                 // l'écran avance d'un cran à la fois vers l'étape réelle
    if (montre >= cible) return;
    const t = window.setTimeout(() => setMontre(m => Math.min(cible, m + 1)), 420);
    return () => window.clearTimeout(t);
  }, [montre, cible]);
  const fini = etat?.etat === 'fini' && montre >= cible;
  useEffect(() => { if (fini) relire(); }, [fini]);   // eslint-disable-line react-hooks/exhaustive-deps
  const lancer = async () => {
    setAttente(true); setErreur(''); setGarde(''); setMontre(0);
    try { setEtat(await api.lancerFabrication(projet.id)); }
    catch (err) { setErreur(err instanceof Error ? err.message : 'Lancement impossible'); }
    finally { setAttente(false); }
  };
  const garder = async () => {
    setGarde('');
    try {
      const r = await api.garderModeleFabrique(projet.id);
      setGarde(`${r.deja ? 'Déjà dans vos modèles 3D' : 'Ajouté à vos modèles 3D'} : « ${r.titre} ». Ouvrez-le depuis l’Accueil, « Modèles 3D ».`);
    } catch (err) { setGarde(err instanceof Error ? err.message : 'Modèle non gardé'); }
  };
  if (!etat || etat.etat === 'indisponible') return null;
  const total = etat.sur ?? 0;
  const travaille = enCours || (etat.etat === 'fini' && !fini);
  const etapes = (etat.etapes ?? []).slice(0, montre);
  const r = etat.resultat;
  return (
    <Panel title="Construire le projet" subtitle={`Par le programme ${etat.programme} du projet`} icon={<Hammer size={18} />}>
      <div className="prj-fab">
        <div className="prj-fab-tete">
          <button className="btn" onClick={() => void lancer()} disabled={attente || travaille}>
            {travaille ? <Spinner /> : <Hammer size={14} />} {etat.etat === 'jamais' ? 'Tout construire' : 'Reconstruire'}
          </button>
          {travaille && <strong>Étape {Math.max(1, montre)} sur {total || '…'} : {etapes[etapes.length - 1] ?? etat.quoi ?? 'Démarrage'}</strong>}
          {fini && <Badge tone="green">Construit</Badge>}
          {etat.etat === 'echec' && <Badge tone="red">Échec</Badge>}
        </div>
        {(travaille || fini) && (
          <div className={`prj-barre ${travaille ? 'active' : ''}`} role="progressbar" aria-valuemin={0} aria-valuemax={total || 1} aria-valuenow={montre}>
            <span style={{ width: `${total ? Math.round(100 * montre / total) : 4}%` }} />
          </div>
        )}
        {etapes.length > 0 && (travaille || fini) && (
          <ul className="prj-etapes">
            {etapes.map((nom, i) => (
              <li key={`${i}-${nom}`} className={travaille && i === etapes.length - 1 ? 'encours' : ''}>
                {travaille && i === etapes.length - 1 ? <Spinner size={12} /> : <CircleCheck size={14} />} {nom}
              </li>
            ))}
          </ul>
        )}
        {erreur && <Unavailable what="La construction" reason={erreur} />}
        {etat.etat === 'echec' && (
          <>
            <Unavailable what="La construction" reason={etat.erreur ?? 'Le programme n’a pas fini.'} />
            {etat.journal && etat.journal.length > 0 && <details><summary>Dernières lignes du programme</summary><ul>{etat.journal.map((l, i) => <li key={i}><code>{l}</code></li>)}</ul></details>}
          </>
        )}
        {fini && r && (
          <>
            <div className="prj-verdict ok" role="status">
              <CircleCheck size={20} />
              <div>
                <strong>{r.resume || 'Construction finie.'}</strong>
                <span>Modèle : <code>{r.modele}</code> ({taille(r.taille)}){r.rapport && <> · Rapport : <code>{r.rapport}</code></>}</span>
              </div>
            </div>
            <div className="prj-fab-tete">
              <button className="btn btn-sm" onClick={() => void garder()}>Garder le modèle dans AURORA</button>
              {garde && <span className="prj-note">{garde}</span>}
            </div>
            <p className="prj-note">Le programme a mis {fmtNum(etat.secondes ?? 0, 1)} s ; la ligne avance à l’écran un cran à la fois pour rester lisible. « Construit » veut dire que le modèle est écrit, pas que le robot est prêt à fabriquer : voir les vérifications.</p>
          </>
        )}
      </div>
    </Panel>
  );
}

/** 15/11/2026, ID Studio IA : « ajoute un visuel de fiche technique […] tout en haut ». L'image que le projet déclare sous le
 *  nom « fiche-technique » (GET /api/projets/<id>/fiche), telle quelle. `maj` : la date du dossier, pour la relire
 *  quand le projet change. Un clic l'agrandit à sa taille réelle. */
function FicheTechnique({ id, titre, maj }: { id: string; titre: string; maj: string }) {
  const [adresse, setAdresse] = useState('');
  const [erreur, setErreur] = useState('');
  const [grande, setGrande] = useState(false);
  useEffect(() => {
    let actif = true, faite = '';
    setErreur('');
    api.projetFiche(id).then(image => {
      if (!actif) return;
      faite = URL.createObjectURL(image);
      setAdresse(faite);
    }).catch(e => { if (actif) { setAdresse(''); setErreur(e instanceof Error ? e.message : String(e)); } });
    return () => { actif = false; if (faite) URL.revokeObjectURL(faite); };
  }, [id, maj]);
  return (
    <Panel title="Fiche technique" subtitle={`${titre} · une image rangée dans le dossier du projet`} icon={<FileImage size={18} />}>
      {erreur ? <Unavailable what="La fiche technique" reason={erreur} />
        : adresse ? <figure className={`prj-fiche ${grande ? 'grande' : ''}`}><img src={adresse} alt={`Fiche technique : ${titre}`} onClick={() => setGrande(g => !g)} /></figure>
          : <Spinner />}
    </Panel>
  );
}

/** Les vérifications qui nomment leurs fichiers (15/11/2026) : un clic les fait tous contrôler par FreeCAD ou KiCad
 *  (tools/moteurs_cao.py verifier_tout), puis le projet est relu : c'est le service qui recalcule leur état. */
function ControlesMoteurs({ projet, relire }: { projet: ProjetDetail; relire: () => void }) {
  const [recu, setRecu] = useState<ProjetControles | null>(null);
  const [erreur, setErreur] = useState('');
  const [enCours, setEnCours] = useState(false);
  const lancer = async () => {
    setEnCours(true); setErreur('');
    try { setRecu(await api.projetVerifierTout(projet.id)); relire(); }
    catch (err) { setRecu(null); setErreur(err instanceof Error ? err.message : 'Service indisponible'); }
    finally { setEnCours(false); }
  };
  const fichiers = new Set(projet.liste_verifications.flatMap(v => v.controle ?? [])).size;
  const raisons = recu ? [...new Set(recu.refuses.map(r => r.raison))] : [];
  return (
    <>
      <div className="prj-controles">
        <button className="btn btn-sm" onClick={() => void lancer()} disabled={enCours}>
          {enCours ? <Spinner /> : <RefreshCw size={14} />} {enCours ? 'Vérification…' : `Faire vérifier ${fichiers} fichier${fichiers > 1 ? 's' : ''} par les moteurs`}
        </button>
        <span className="prj-note">Une vérification marquée « calculée » n’est faite que si FreeCAD ou KiCad a dit « conforme » de chacun de ses fichiers, tel qu’il est aujourd’hui.</span>
      </div>
      {erreur && <Unavailable what="La vérification par les moteurs" reason={erreur} />}
      {recu && (
        <p className="prj-note" role="status">
          {recu.verifies.length} fichier{recu.verifies.length > 1 ? 's' : ''} vérifié{recu.verifies.length > 1 ? 's' : ''}
          {recu.refuses.length > 0 ? ` ; ${recu.refuses.length} non vérifié${recu.refuses.length > 1 ? 's' : ''} : ${raisons.join(' ')}` : '.'}
        </p>
      )}
    </>
  );
}

function Dossier({ projet, outils = [], ouvrir, relire }: {
  projet: ProjetDetail; outils?: OutilConception[]; ouvrir?: (f: ProjetFichier, outil: OutilConception) => void;
  relire?: () => void;
}) {
  return (
    <>
      <div className={`prj-verdict ${projet.pret_a_fabriquer ? 'ok' : ''}`} role="status">
        {projet.pret_a_fabriquer ? <CircleCheck size={20} /> : <TriangleAlert size={20} />}
        <div>
          <strong>{projet.pret_a_fabriquer ? 'Prêt à fabriquer' : 'Pas prêt à fabriquer'}</strong>
          {projet.pret_a_fabriquer ? <span>Toutes les vérifications écrites sont faites, et aucun fichier ne manque.</span>
            : <ul>{projet.pourquoi_pas.map(raison => <li key={raison}>{raison}</li>)}</ul>}
        </div>
      </div>
      <p className="prj-note">{projet.liste_verifications.some(v => v.calcule)
        ? 'Les états ci-dessous sont ceux que le dossier du projet déclare, sauf les vérifications marquées « calculée » : leur état vient de ce que FreeCAD ou KiCad a dit de leurs fichiers. AURORA mesure aussi la présence de chaque fichier.'
        : 'Les états ci-dessous sont ceux que le dossier du projet déclare. AURORA vérifie elle-même une seule chose pour l’instant : la présence de chaque fichier. Faire calculer les vérifications par le code est l’étape suivante.'}</p>

      <Panel title="Cahier des charges" subtitle={`${projet.cahier.length} exigence${projet.cahier.length > 1 ? 's' : ''}`} icon={<ClipboardList size={18} />}>
        {projet.cahier.length ? (
          <table className="prj-table"><tbody>
            {projet.cahier.map(e => (
              <tr key={e.texte}>
                <td><Badge tone={EXIGENCE[e.etat]?.[1] ?? 'default'}>{EXIGENCE[e.etat]?.[0] ?? e.etat}</Badge></td>
                <td>{e.texte}{e.note && <small>{e.note}</small>}</td>
              </tr>
            ))}
          </tbody></table>
        ) : <EmptyState>Aucune exigence écrite.</EmptyState>}
      </Panel>

      {projet.fabrication && <Fabrication key={`fab-${projet.id}`} projet={projet} relire={relire ?? (() => undefined)} />}

      {projet.calcul && <Calcul key={projet.id} projet={projet} />}

      <Panel title="Vérifications avant fabrication" subtitle={`${projet.verifications.faites} faite${projet.verifications.faites > 1 ? 's' : ''} sur ${projet.verifications.total}`} icon={<CircleCheck size={18} />}>
        {projet.liste_verifications.some(v => v.calcule) && <ControlesMoteurs key={`ctl-${projet.id}`} projet={projet} relire={relire ?? (() => undefined)} />}
        {projet.liste_verifications.length ? (
          <table className="prj-table"><tbody>
            {projet.liste_verifications.map(v => (
              <tr key={v.quoi}>
                <td><Badge tone={VERIFICATION[v.etat]?.[1] ?? 'default'}>{VERIFICATION[v.etat]?.[0] ?? v.etat}</Badge></td>
                <td>{v.quoi}{v.detail && <small>{v.detail}</small>}
                  {v.calcule && <small className="prj-calcule"><Badge tone="cyan" title="Son état n’est pas déclaré : il vient de ce que le moteur a dit de ses fichiers.">calculée</Badge> {v.calcule.texte}</small>}
                </td>
              </tr>
            ))}
          </tbody></table>
        ) : <EmptyState>Aucune vérification écrite : le projet ne peut pas être dit prêt.</EmptyState>}
      </Panel>

      <Panel title="Fichiers du projet" subtitle={projet.dossier} icon={<FolderKanban size={18} />}>
        <div className="prj-rubriques">
          {projet.rubriques.map(r => (
            <div className="prj-rubrique" key={r.id}>
              <h4>{r.titre}</h4>
              {r.note && <p>{r.note}</p>}
              {r.fichiers.length ? (
                <ul className="prj-fichiers">
                  {r.fichiers.map(f => {
                    const outil = ouvrir ? outilPour(f, outils) : undefined;
                    const moteur = outilPour(f, outils, 'verifie');
                    return (
                      <li key={f.chemin}><EtatFichier f={f} /><span title={f.role}>{f.nom} <code>{f.chemin}</code>
                        {outil && ouvrir && <button className="btn btn-sm prj-ouvrir" onClick={() => ouvrir(f, outil)}>Ouvrir dans {outil.autorise}</button>}
                        {moteur && <VerifierFichier key={`${projet.id}:${f.chemin}`} projet={projet.id} f={f} outil={moteur}
                          apres={projet.liste_verifications.some(v => v.controle?.includes(f.chemin)) ? relire : undefined} />}
                      </span></li>
                    );
                  })}
                </ul>
              ) : <p className="prj-note">Aucun fichier.</p>}
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

/** 23/11/2026, ID Studio IA : « une icône création de projet ; on laisse la fiche technique vide sans les modèles 3D ». Un
 *  projet vide (POST /api/projets) : un titre, un résumé. Le service l'écrit dans les projets de la personne, jamais
 *  par-dessus un dossier qui existe ; ses fichiers, sa fiche et son modèle 3D viennent ensuite, par son dossier. */
function NouveauProjet({ fermer, cree }: { fermer: () => void; cree: (p: ProjetDetail) => void }) {
  const [titre, setTitre] = useState('');
  const [resume, setResume] = useState('');
  const [erreur, setErreur] = useState('');
  const [enCours, setEnCours] = useState(false);
  const pret = titre.trim().length >= 2 && !enCours;
  const creer = async () => {
    setEnCours(true); setErreur('');
    try { cree(await api.creerProjet(titre, resume)); }
    catch (err) {
      const dit = err instanceof Error ? err.message : 'Service indisponible';
      // Un service démarré avant cette fonction ne connaît pas la route : le dire, plutôt que son message en anglais.
      setErreur(dit === 'Method Not Allowed' ? 'Ce service d’AURORA a démarré avant cette fonction : redémarrez AURORA, puis réessayez.' : dit);
    } finally { setEnCours(false); }
  };
  return (
    <Panel title="Nouveau projet" subtitle="Créé vide, dans vos données d’AURORA" icon={<FolderPlus size={18} />}>
      <form className="prj-creer" onSubmit={e => { e.preventDefault(); if (pret) void creer(); }}>
        <label className="field">Titre
          <input className="input" value={titre} maxLength={100} autoFocus onChange={e => setTitre(e.target.value)} placeholder="Lampe de bureau, station météo…" />
        </label>
        <label className="field">Résumé (facultatif)
          <textarea className="textarea" rows={2} value={resume} maxLength={600} onChange={e => setResume(e.target.value)} placeholder="À quoi il sert, en une ou deux phrases." />
        </label>
        <div className="prj-controles">
          <button type="submit" className="btn btn-sm btn-primary" disabled={!pret}>{enCours ? <Spinner /> : <FolderPlus size={14} />} Créer</button>
          <button type="button" className="btn btn-sm" onClick={fermer} disabled={enCours}>Annuler</button>
          <span className="prj-note">Le projet démarre sans fichier, sans fiche technique et sans modèle 3D : ils s’affichent ici quand son dossier les déclare. AURORA ne sait pas encore les y ranger à votre place.</span>
        </div>
        {erreur && <Unavailable what="La création du projet" reason={erreur} />}
      </form>
    </Panel>
  );
}

export default function ProjectsPage() {
  const [liste, setListe] = useState<ProjetsListe | null>(null);
  const [creation, setCreation] = useState(false);
  const [erreur, setErreur] = useState('');
  const [choisi, setChoisi] = useState<string | null>(null);
  const [projet, setProjet] = useState<ProjetDetail | null>(null);
  const [lecture, setLecture] = useState(false);
  // FreeCAD et KiCad : un service qui ne connaît pas encore la route ne montre ni le panneau ni les boutons.
  const [outils, setOutils] = useState<OutilConception[]>([]);
  const { navigate } = useAurora();
  const send = useSend();
  // Le bouton dit la même phrase que vous : elle passe par le chemin habituel (liste autorisée, journal des étapes).
  const ouvrir = useCallback((f: ProjetFichier, outil: OutilConception) => {
    if (projet && outil.autorise) void send(`ouvre « ${f.nom} » du projet ${projet.id} dans ${outil.autorise}`, { source: 'text', openDrawer: true, withAttachments: false });
  }, [projet, send]);

  const lire = useCallback(async () => {
    setLecture(true); setErreur('');
    api.outilsConception().then(r => setOutils(r.outils)).catch(() => setOutils([]));
    try {
      const recu = await api.projets();
      setListe(recu);
      setChoisi(avant => avant && recu.projets.some(p => p.id === avant) ? avant : recu.projets[0]?.id ?? null);
    } catch (err) {
      setErreur(err instanceof Error ? err.message : 'Service indisponible');
    } finally { setLecture(false); }
  }, []);
  useEffect(() => { void lire(); }, [lire]);
  useEffect(() => {
    if (!choisi) { setProjet(null); return; }
    let actif = true;
    api.projet(choisi).then(p => { if (actif) setProjet(p); }).catch(() => { if (actif) setProjet(null); });
    return () => { actif = false; };
  }, [choisi, liste]);

  return (
    <div className="prj-page">
      <div className="page-title">
        <div>
          <div className="eyebrow">Conception</div>
          <h1>Projets</h1>
          <p>Chaque projet garde son cahier des charges, ses calculs, ses pièces, son électronique et ses vérifications, pas seulement son modèle 3D.</p>
        </div>
        <div className="prj-actions">
          <button className="btn btn-sm btn-primary" onClick={() => setCreation(true)} disabled={creation}><FolderPlus size={14} /> Créer un projet</button>
          <button className="btn btn-sm" onClick={() => void lire()} disabled={lecture}>{lecture ? <Spinner /> : <RefreshCw size={14} />} Relire</button>
        </div>
      </div>

      {creation && <NouveauProjet fermer={() => setCreation(false)}
        cree={p => { setCreation(false); void lire().then(() => setChoisi(p.id)); }} />}
      {erreur && <Unavailable what="La liste des projets" reason={erreur} />}
      {liste && !liste.projets.length && !erreur && !creation && <EmptyState>Aucun projet pour l’instant. Le bouton « Créer un projet » en commence un.</EmptyState>}
      {liste && liste.projets.length > 0 && (
        <div className="prj-liste">
          {liste.projets.map(p => (
            <button key={p.id} className={`prj-carte ${choisi === p.id ? 'active' : ''}`} onClick={() => setChoisi(p.id)} aria-pressed={choisi === p.id}>
              <div className="prj-ligne">
                <Badge tone={p.pret_a_fabriquer ? 'green' : 'amber'}>{p.pret_a_fabriquer ? 'Prêt à fabriquer' : 'Pas prêt à fabriquer'}</Badge>
                {p.stade && <Badge>{p.stade}</Badge>}
              </div>
              <h3>{p.titre}</h3>
              {p.resume && <p>{p.resume}</p>}
              <span className="prj-compte">
                {p.verifications.faites} vérification{p.verifications.faites > 1 ? 's' : ''} faite{p.verifications.faites > 1 ? 's' : ''} sur {p.verifications.total}
                {p.verifications.bloquantes > 0 && ` · ${p.verifications.bloquantes} bloquante${p.verifications.bloquantes > 1 ? 's' : ''}`}
                {p.maj && ` · mis à jour le ${p.maj}`}
              </span>
            </button>
          ))}
        </div>
      )}
      {liste && liste.refuses.length > 0 && (
        <Panel title="Dossiers non lus" subtitle="Leur projet.json est refusé" icon={<TriangleAlert size={18} />}>
          <ul className="prj-fichiers">{liste.refuses.map(r => <li key={r.dossier}><Badge tone="red">refusé</Badge><span>{r.raison} <code>{r.dossier}</code></span></li>)}</ul>
        </Panel>
      )}
      {projet?.fiche_donnees ? (
        <Panel title="Fiche technique" subtitle={`${projet.titre} · cliquez sur une vue pour l’ouvrir en 3D`} icon={<FileImage size={18} />}>
          <Suspense fallback={<Spinner />}>
            <FicheInteractive projet={projet} cle={projet.rubriques.flatMap(r => r.fichiers)
              .filter(f => f.chemin === projet.fiche_donnees || f.chemin.toLowerCase().endsWith('.glb')).map(f => f.taille).join('-')} />
          </Suspense>
        </Panel>
      ) : projet?.fiche ? <FicheTechnique id={projet.id} titre={projet.titre}
        maj={`${projet.maj}-${projet.rubriques.flatMap(r => r.fichiers).find(f => f.chemin === projet.fiche)?.taille ?? 0}`} />
        : projet && (
          // 23/11/2026 : un projet qui vient d'être créé n'a ni fiche ni modèle 3D. Le cadre reste là, vide, et le dit.
          <Panel title="Fiche technique" subtitle={`${projet.titre} · aucune pour l’instant`} icon={<FileImage size={18} />}>
            <EmptyState>Ce projet n’a pas encore de fiche technique ni de modèle 3D. Ils s’afficheront ici quand son dossier déclarera un fichier « fiche-technique » (une image) ou « fiche-technique.json » (des cadres et un modèle 3D).</EmptyState>
          </Panel>
        )}
      {outils.length > 0 && <Logiciels outils={outils} versParametres={() => navigate('settings')} />}
      {projet && <Dossier projet={projet} outils={outils} ouvrir={ouvrir} relire={() => void lire()} />}
    </div>
  );
}
