"""Banc du 11/11/2026 : ce que le modèle local écrit quand on lui demande une maquette 3D, avant et après une retouche
de la consigne (ID Studio IA : « améliorer les modèles techniques du modèle, voilà son X-wing, rien n'est en place »).

Pour chaque objet : la même consigne et le même schéma que le service (tools/lab.py), envoyés à Ollama sur ce PC ;
la réponse passe par lab.build_generated, dans un dossier AURORA de banc (jamais le vrai). Les scènes obtenues sont
dessinées ensuite par croquis.py (qui a besoin de Pillow, absent de l'environnement d'AURORA).

Depuis le 12/11/2026 (ID Studio IA : « un vrai générateur d'hologramme comme tu fais toi ») le banc suit les mêmes étapes que le
service : le contrôle du code, puis une correction par le même modèle quand le contrôle a des remarques. Pour un objet
corrigé il garde, à côté de la scène finale, le brouillon (<objet>-brouillon.json), les remarques, la correction
écrite par le modèle et les remarques qui restent.

    <python d'AURORA> banc_maquettes.py <variante> <modèle Ollama> <dossier de sortie> [température]

PYTHONPATH sur le dossier backend ; AURORA_HOME et AURORA_DB_PATH sur un dossier de banc (contrôlé plus bas).
"""
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
maison = Path(os.environ.get('AURORA_HOME', ''))
if 'banc-maquettes' not in str(maison):
    sys.exit(f'refusé : AURORA_HOME ({maison}) n’est pas le dossier du banc')

from aurora.core import prompts  # noqa: E402
from aurora.tools import lab  # noqa: E402

OBJETS = [('x_wing', 'un X-wing de style réaliste mais simple'), ('voiture', 'une voiture'),
          ('avion', 'un avion de ligne'), ('helicoptere', 'un hélicoptère'), ('voilier', 'un voilier'),
          ('eolienne', 'une éolienne'),
          ('robot', 'un robot')]   # 13/11/2026 : l'objet qu’ID Studio IA avait demandé à AURORA (« pieds trop grands »)


def demander(modele: str, systeme: str, contenu: str, temperature: float | None,
             schema: dict | None = None) -> tuple[str, float]:
    options = {'num_predict': 4000, 'num_ctx': 8192}
    if temperature is not None:
        options['temperature'] = temperature
    corps = json.dumps({'model': modele, 'stream': False, 'think': False, 'format': schema or lab.generation_schema(),
                        'options': options, 'messages': [{'role': 'system', 'content': systeme},
                                                         {'role': 'user', 'content': contenu}]}).encode('utf-8')
    debut = time.time()
    requete = urllib.request.Request('http://127.0.0.1:11434/api/chat', data=corps,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(requete, timeout=400) as reponse:
        texte = json.loads(reponse.read().decode('utf-8'))['message']['content']
    return texte, time.time() - debut


def main() -> None:
    variante, modele, sortie = sys.argv[1], sys.argv[2], Path(sys.argv[3]) / sys.argv[1]
    temperature = float(sys.argv[4]) if len(sys.argv) > 4 else None
    shutil.rmtree(maison / lab.GENERATED_SUBDIR, ignore_errors=True)
    sortie.mkdir(parents=True, exist_ok=True)
    bilan = []
    for cle, sujet in OBJETS:
        try:   # la consigne dépend de l'objet depuis le 11/11/2026 (l'exemple le plus proche)
            consigne = lab.generation_instructions(sujet)
        except TypeError:
            consigne = lab.generation_instructions()
        systeme = prompts.system_prompt_parts({}, purpose='scene', extra=consigne)[0]
        (sortie / f'consigne-{cle}.txt').write_text(systeme, encoding='utf-8')
        contenu = (f'### Demande\nFais-moi un modèle 3D de {sujet.split(" ", 1)[1]}\n\n'
                   f'### Objet à construire en trois dimensions\n{sujet}')
        try:
            texte, duree = demander(modele, systeme, contenu, temperature)
        except Exception as erreur:   # le banc continue : l'échec est noté
            bilan.append(f'{cle}: ÉCHEC de la demande ({erreur})')
            print(bilan[-1], flush=True)
            continue
        (sortie / f'{cle}.txt').write_text(texte, encoding='utf-8')
        if not hasattr(lab, 'improve_generated'):   # le code d'avant le 12/11/2026 : une seule passe
            scene, raison = lab.build_generated(texte, sujet, f'banc ({modele})')
            remarques, suite = [], ''
        else:
            # 12/11/2026 : comme le service (core/orchestrator.py) : le contrôle du code, puis UNE correction par le
            # même modèle si le contrôle a des remarques ; la maquette corrigée ne remplace la première que si le
            # contrôle en a moins.
            remarques = []
            scene, raison = lab.build_generated(texte, sujet, f'banc ({modele})', remarks=remarques)
            suite = f', {len(remarques)} remarque(s)'
            if scene is not None and remarques:
                brouillon = dict(scene, _banc=f'{variante} (brouillon) | {modele} | {sujet}')
                (sortie / f'{cle}-brouillon.json').write_text(json.dumps(brouillon, ensure_ascii=False, indent=1),
                                                             encoding='utf-8')
                (sortie / f'{cle}-remarques.txt').write_text('\n'.join(remarques), encoding='utf-8')
                try:
                    correction, plus = demander(modele, systeme, lab.correction_request(sujet, texte, remarques),
                                                temperature, lab.correction_schema())
                except Exception as erreur:
                    correction, plus = '', 0.0
                    suite += f' ; correction en ÉCHEC ({erreur})'
                duree += plus
                (sortie / f'{cle}-correction.txt').write_text(correction, encoding='utf-8')
                corrigee, reste, pourquoi = lab.improve_generated(scene['id'], texte, correction, sujet,
                                                                  f'banc ({modele})',
                                                                  fingerprint=lab.scene_fingerprint(scene['id']))
                if corrigee is not None:
                    scene = corrigee
                    suite += f' ; corrigée en {plus:.0f} s, il en reste {len(reste)}'
                    (sortie / f'{cle}-restent.txt').write_text('\n'.join(reste), encoding='utf-8')
                else:
                    suite += f' ; correction non retenue ({pourquoi})'
        if scene is None:
            bilan.append(f'{cle}: refusée ({raison}) en {duree:.0f} s')
            print(bilan[-1], flush=True)
            continue
        scene['_banc'] = f'{variante} | {modele} | {sujet}'
        (sortie / f'{cle}.json').write_text(json.dumps(scene, ensure_ascii=False, indent=1), encoding='utf-8')
        bilan.append(f'{cle}: {len(scene["pieces"])} pièces en {duree:.0f} s{suite}')
        print(bilan[-1], flush=True)
    (sortie / 'bilan.txt').write_text('\n'.join(bilan), encoding='utf-8')
    print('fini', flush=True)


if __name__ == '__main__':
    main()
