// Morceaux de shaders partagés par les scènes three.js d'AURORA : le globe de Monde 3D (scene.ts) et le noyau de
// l'accueil (components/labo/noyau.ts). Dans un fichier à part pour que l'accueil ne charge pas tout le globe (08/11/2026).

/** Coordonnées de texture seules : pour les disques et les images posées. */
export const VERTEX_UV = 'varying vec2 vUv;void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}';
/** Normale (vue) et position sur la sphère unité : pour les halos en bord de sphère (fresnel). */
export const VERTEX_NORMALE = 'varying vec3 n;varying vec3 p;void main(){p=normalize(position);n=normalize(normalMatrix*normal);gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}';
