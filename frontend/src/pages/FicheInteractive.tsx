// La fiche technique interactive d'un projet (15/11/2026). ID Studio IA : « fait la fiche technique interactive avec les model
// 3D et les animation quant on clique sur le cadre et grades le style de la fiche technique si je veux crée un nouveaux
// projet et que les cadre change suivent le model du projet ».
// Les cadres viennent du projet lui-même : son programme les écrit dans « fiche-technique.json », le service les valide
// (GET /api/projets/<id>/fiche/donnees), cette page les dessine au style d'un plan. Un cadre de vue montre le modèle 3D
// du projet (GET …/fiche/modele) : une image fixe d'abord, puis, au clic, le modèle vivant dans le cadre (il tourne à
// la souris, s'éclate, joue l'animation du fichier ; un clic sur une pièce montre sa fiche). Un seul cadre est vivant à
// la fois. L'état du projet est celui que son dossier déclare.
// Honnêteté : les chiffres sont ceux que le programme du projet a écrits ; une animation lue est celle du fichier.
import './fiche.css';
import { useEffect, useRef, useState } from 'react';
import { Eye, Layers, Play, RotateCcw, Square, X } from 'lucide-react';
import { api } from '../lib/api';
import type { FicheCadre, FicheDonnees, ProjetDetail } from '../lib/types';
import type { PieceFichier } from '../components/labo/scene';
import { Spinner, Unavailable } from '../components/ui';
import type { AtelierVues, ImageVue, VueVivante } from './ficheVues';

type EtatModele = 'attente' | 'lecture' | 'pret' | 'absent' | 'panne';
const dit = (e: unknown): string => (e instanceof Error ? e.message : String(e));
const placer = (c: FicheCadre): React.CSSProperties | undefined =>
  (c.place.length === 4 ? { gridColumn: `${c.place[0]} / span ${c.place[2]}`, gridRow: `${c.place[1]} / span ${c.place[3]}` } : undefined);

/** Les cotes écrites d'une vue, tracées sur son image fixe : sous le modèle sa largeur, à sa gauche sa hauteur. */
function Cotes({ cadre, image }: { cadre: FicheCadre; image: ImageVue }) {
  const [gauche, droite, haut, bas] = image.boite.map(v => v * 100);
  return (
    <>
      {cadre.cote_largeur && <span className="fiche-cote large" style={{ left: `${gauche}%`, width: `${droite - gauche}%`, top: `${Math.min(bas + 2.5, 93)}%` }}>{cadre.cote_largeur}</span>}
      {cadre.cote_hauteur && <span className="fiche-cote haute" style={{ top: `${haut}%`, height: `${bas - haut}%`, left: `${Math.max(gauche - 5, 7)}%` }}><span>{cadre.cote_hauteur}</span></span>}
    </>
  );
}

/** L'état du projet, tel que son dossier le déclare : ses vérifications faites, à faire et bloquantes. */
function EtatProjet({ projet }: { projet: ProjetDetail }) {
  const v = projet.verifications, liste = projet.liste_verifications;
  const bloquantes = liste.filter(x => x.etat === 'bloquante'), aFaire = liste.filter(x => x.etat === 'a_faire');
  return (
    <div className="fiche-etat">
      <p className="fiche-comptes">
        <span className="faites">{v.faites} vérification{v.faites > 1 ? 's' : ''} faite{v.faites > 1 ? 's' : ''}</span>
        <span className="a-faire">{v.a_faire} à faire</span>
        <span className="bloquantes">{v.bloquantes} bloquante{v.bloquantes > 1 ? 's' : ''}</span>
      </p>
      {bloquantes.length > 0 && <><h4 className="bloquant">Bloquant :</h4><ul>{bloquantes.map(x => <li key={x.quoi} className="bloquant"><span>{x.quoi}</span></li>)}</ul></>}
      {aFaire.length > 0 && <><h4 className="a-faire">À faire :</h4><ul>{aFaire.map(x => <li key={x.quoi} className="a-faire"><span>{x.quoi}</span></li>)}</ul></>}
    </div>
  );
}

export default function FicheInteractive({ projet, cle }: { projet: ProjetDetail; cle: string }) {
  const [donnees, setDonnees] = useState<FicheDonnees | null>(null);
  const [erreur, setErreur] = useState('');
  const [etat, setEtat] = useState<EtatModele>('attente');
  const [panne, setPanne] = useState('');
  const [images, setImages] = useState<Record<number, ImageVue>>({});
  const [capacites, setCapacites] = useState<{ animations: string[]; pieces: boolean; coque: boolean }>({ animations: [], pieces: false, coque: false });
  const [ouvert, setOuvert] = useState<number | null>(null);          // le cadre de vue qui est vivant
  const [eclate, setEclate] = useState(false);
  const [lue, setLue] = useState<number | null>(null);
  const [dedans, setDedans] = useState(false);
  const [piece, setPiece] = useState<PieceFichier | null>(null);
  const atelier = useRef<AtelierVues | null>(null);
  const vivante = useRef<VueVivante | null>(null);
  const zones = useRef<Record<number, HTMLDivElement | null>>({});
  const toiles = useRef<Record<number, HTMLDivElement | null>>({});
  const grille = useRef<HTMLDivElement | null>(null);
  const [largeur, setLargeur] = useState(0);                          // la largeur de la fiche : les images fixes la suivent

  // 1. Les cadres de la fiche, relus quand le projet ou ses données changent.
  useEffect(() => {
    let actif = true;
    setDonnees(null); setErreur(''); setOuvert(null);
    api.projetFicheDonnees(projet.id).then(d => { if (actif) setDonnees(d); }).catch(e => { if (actif) setErreur(dit(e)); });
    return () => { actif = false; };
  }, [projet.id, cle]);

  // 2. Le modèle 3D, lu une fois les cadres dessinés (chaque image fixe prend la taille de son cadre).
  useEffect(() => {
    if (!donnees) return;
    setImages({}); setPanne('');
    if (!donnees.modele || !donnees.fiche.cadres.some(c => c.type === 'vue')) { setEtat('absent'); return; }
    let actif = true;
    setEtat('lecture');
    void (async () => {
      try {
        const [{ creerAtelier }, octets] = await Promise.all([import('./ficheVues'), api.projetFicheModele(projet.id)]);
        if (!actif) return;
        const a = await creerAtelier(octets);
        if (!actif) { a.liberer(); return; }
        atelier.current = a;
        setCapacites({ animations: a.animations, pieces: a.pieces, coque: a.coque });
        setEtat('pret');
      } catch (e) {
        if (actif) { setPanne(dit(e)); setEtat('panne'); }
      }
    })();
    return () => { actif = false; atelier.current?.liberer(); atelier.current = null; };
  }, [donnees, projet.id]);

  // 2 bis. Les images fixes des vues, à la taille de leur cadre : faites quand le modèle est prêt, refaites quand la
  // fiche change de largeur ou qu'une vue vivante se referme (le moteur de rendu est alors libre).
  useEffect(() => {
    const el = grille.current;
    if (!el) return;
    let attente = 0;
    const veille = new ResizeObserver(() => {
      window.clearTimeout(attente);
      attente = window.setTimeout(() => setLargeur(Math.round(el.getBoundingClientRect().width)), 250);
    });
    veille.observe(el);
    return () => { veille.disconnect(); window.clearTimeout(attente); };
  }, [donnees]);
  useEffect(() => {
    const a = atelier.current;
    if (etat !== 'pret' || ouvert !== null || !a || !donnees) return;
    const finesse = Math.min(2, window.devicePixelRatio || 1), faites: Record<number, ImageVue> = {};
    donnees.fiche.cadres.forEach((c, i) => {
      if (c.type !== 'vue') return;
      const r = zones.current[i]?.getBoundingClientRect();
      faites[i] = a.image(c, (r && r.width > 20 ? r.width : 480) * finesse, (r && r.height > 20 ? r.height : 400) * finesse);
    });
    setImages(faites);
  }, [etat, largeur, ouvert, donnees]);

  // 3. La vue vivante du cadre ouvert ; elle se referme quand un autre cadre s'ouvre ou que la fiche change.
  useEffect(() => {
    const cadre = ouvert === null ? undefined : donnees?.fiche.cadres[ouvert], toile = ouvert === null ? null : toiles.current[ouvert];
    if (!cadre || !toile || !atelier.current) return;
    setEclate(cadre.eclate); setLue(null); setDedans(false); setPiece(null);
    const vue = atelier.current.ouvrir(toile, cadre, setPiece);
    vivante.current = vue;
    return () => { vue.fermer(); if (vivante.current === vue) vivante.current = null; };
  }, [ouvert, donnees, etat]);

  if (erreur) return <Unavailable what="La fiche technique" reason={erreur} />;
  if (!donnees) return <div className="fiche-attente" style={{ position: 'static' }}><Spinner /> Lecture de la fiche technique…</div>;
  const f = donnees.fiche;

  const vue = (c: FicheCadre, i: number) => {
    const image = images[i], vivant = ouvert === i, cliquable = etat === 'pret' && !vivant;
    return (
      <>
        <div className={`fiche-zone ${cliquable ? 'cliquable' : ''}`} ref={el => { zones.current[i] = el; }}
          role={cliquable ? 'button' : undefined} tabIndex={cliquable ? 0 : undefined} aria-label={cliquable ? `Ouvrir en 3D : ${c.titre}` : undefined}
          onClick={cliquable ? () => setOuvert(i) : undefined} onKeyDown={cliquable ? e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOuvert(i); } } : undefined}>
          {!vivant && image && <>
            <img src={image.adresse} alt={`${c.titre} : ${f.titre}`} draggable={false} />
            <Cotes cadre={c} image={image} />
            {image.reperes.map(r => <span key={r.n} className="fiche-repere" style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%` }}>{r.n}</span>)}
            <span className="fiche-invite">Cliquer pour ouvrir en 3D</span>
          </>}
          {!vivant && !image && <div className="fiche-attente">
            {etat === 'attente' || etat === 'lecture' ? <><Spinner /> Lecture du modèle 3D…</>
              : etat === 'absent' ? 'Cette fiche n’a pas de modèle 3D : la vue ne peut pas être montrée.'
                : `Modèle 3D indisponible : ${panne || 'il n’a pas pu être lu'}.`}
          </div>}
          <div className="fiche-toile" ref={el => { toiles.current[i] = el; }} style={vivant ? undefined : { display: 'none' }} />
          {vivant && piece && <div className="fiche-piece">
            <button onClick={() => setPiece(null)} aria-label="Fermer la fiche de la pièce">×</button>
            <strong>{piece.nom}</strong>
            <em>{piece.genre === 'coque' ? 'Coque du modèle' : piece.genre === 'piece' ? 'Pièce posée dans le modèle' : 'Pièce du fichier'}</em>
            {piece.role}
            {piece.proprietes.length > 0 && <dl>{piece.proprietes.map(p => <span key={p.nom} style={{ display: 'contents' }}><dt>{p.nom}</dt><dd>{p.valeur}</dd></span>)}</dl>}
          </div>}
        </div>
        {vivant && <div className="fiche-outils">
          {capacites.pieces && <button className="btn btn-sm" onClick={() => { vivante.current?.eclater(!eclate); setEclate(!eclate); }}><Layers size={13} /> {eclate ? 'Remonter' : 'Éclater'}</button>}
          {capacites.animations.map((nom, rang) => (
            <button key={rang} className="btn btn-sm" title="L’animation lue est celle du fichier du modèle." onClick={() => { const suivant = lue === rang ? null : rang; vivante.current?.jouer(suivant); setLue(suivant); }}>
              {lue === rang ? <Square size={13} /> : <Play size={13} />} {lue === rang ? 'Arrêter' : `Lire ${nom ? `« ${nom} »` : `l’animation ${rang + 1}`}`}
            </button>
          ))}
          {capacites.coque && <button className="btn btn-sm" onClick={() => { vivante.current?.interieur(!dedans); setDedans(!dedans); }}><Eye size={13} /> {dedans ? 'Coque pleine' : 'Voir dedans'}</button>}
          <button className="btn btn-sm" onClick={() => vivante.current?.recentrer()}><RotateCcw size={13} /> Vue de départ</button>
          <button className="btn btn-sm" onClick={() => setOuvert(null)}><X size={13} /> Fermer la 3D</button>
        </div>}
      </>
    );
  };

  return (
    <section className="fiche" aria-label={`Fiche technique : ${f.titre}`}>
      {(['haut', 'bas'] as const).map(cote => <div key={cote} className={`fiche-bord ${cote}`} aria-hidden="true">{Array.from({ length: 12 }, (_, n) => <span key={n}>{n + 1}</span>)}</div>)}
      {(['gauche', 'droite'] as const).map(cote => <div key={cote} className={`fiche-bord ${cote}`} aria-hidden="true">{'ABCDEFGH'.split('').map(l => <span key={l}>{l}</span>)}</div>)}
      <div className="fiche-grille" ref={grille}>
        {f.cadres.map((c, i) => (
          <article key={`${i}-${c.titre}`} className={`fiche-cadre ${c.accent === 'orange' ? 'orange' : ''}`} style={placer(c)}>
            <h3 className="fiche-titre" title={c.titre}>{c.titre}</h3>
            {c.type === 'vue' ? vue(c, i) : (
              <div className="fiche-corps">
                {c.type === 'tableau' && <table className="fiche-table"><tbody>{c.lignes.map((ligne, n) => <tr key={n}>{ligne.map((cas, k) => <td key={k}>{cas}</td>)}</tr>)}</tbody></table>}
                {c.type === 'liste' && <ul className="fiche-liste">{c.lignes.map((ligne, n) => (
                  <li key={n}>{c.numerotee ? <span className="fiche-num">{n + 1}</span> : <span className="fiche-puce" />}<span>{ligne.join(' · ')}</span></li>
                ))}</ul>}
                {c.type === 'etat' && <EtatProjet projet={projet} />}
                {c.note && <p className="fiche-note">{c.note}</p>}
              </div>
            )}
          </article>
        ))}
        <div className="fiche-cartouche">
          <div className="fiche-logo" aria-hidden="true">
            <svg viewBox="0 0 120 132"><polygon points="60,4 2,128 26,128 60,56 94,128 118,128" fill="none" stroke="#eaf2fc" strokeWidth="5" strokeLinejoin="miter" /></svg>
            <span>AURORA</span>
            <i><b /><b /><b /></i>
          </div>
          <div className="fiche-nom">
            <h2>{f.titre}</h2>
            {f.sous_titre && <p>{f.sous_titre}</p>}
            {f.note && <small>{f.note}</small>}
          </div>
          {f.cartouche.length > 0 && <div className="fiche-cases">{f.cartouche.map(([nom, valeur]) => <span key={nom} title={`${nom} : ${valeur}`}>{nom} : {valeur}</span>)}</div>}
        </div>
      </div>
    </section>
  );
}
