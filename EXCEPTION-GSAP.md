# Permission additionnelle pour GSAP

AURORA — Copyright (C) 2026 ID Studio IA — est distribuée sous la licence GNU Affero General Public License,
version 3 (fichier `LICENSE`).

L'interface d'AURORA utilise la bibliothèque d'animation GSAP. GSAP n'est pas un logiciel libre : elle est régie par
la [« Standard "No Charge" GSAP License »](https://gsap.com/community/standard-license/) de son éditeur. Sans
permission, la licence d'AURORA ne permettrait pas de redistribuer AURORA assemblée avec elle. Le titulaire des droits
sur AURORA donne donc la permission ci-dessous, comme le prévoit l'article 7 de la licence. Décision du 24/11/2026.

## Le texte de la permission

> Additional permission under GNU AGPL version 3 section 7
>
> If you modify this Program, or any covered work, by linking or combining it with GSAP (or a modified version of
> that library), containing parts covered by the terms of the Standard "No Charge" GSAP License, the licensors of
> this Program grant you additional permission to convey the resulting work.

Ce texte reprend le modèle publié par la Free Software Foundation
([foire aux questions des licences GNU](https://www.gnu.org/licenses/gpl-faq.html#GPLIncompatibleLibs), lue le
24/11/2026). Trois retouches seulement, toutes prévues ou imposées par le cas :

- les crochets du modèle sont remplis avec le nom de la bibliothèque et celui de sa licence ;
- « GNU GPL » est devenu « GNU AGPL », la licence d'AURORA ;
- la phrase facultative entre accolades, sur le code source de la bibliothèque, est retirée, comme le modèle le
  demande quand tout le monde ne peut pas redistribuer ce code source.

## En mots simples

- Vous pouvez redistribuer AURORA, modifiée ou non, avec GSAP dedans (par exemple une interface déjà construite)
  sans enfreindre la licence d'AURORA.
- Cette permission ne change rien à la licence de GSAP : GSAP reste la propriété de Webflow, et ses propres conditions
  s'appliquent toujours à qui l'utilise ou la redistribue.
- Celui qui modifie AURORA n'est pas obligé de garder cette permission pour sa propre version.
- Tout le reste d'AURORA reste sous la licence AGPL version 3, sans exception.

## Fichier concerné

`frontend/src/components/flow/FlowReplay.tsx` : c'est le seul fichier d'AURORA qui appelle GSAP.
