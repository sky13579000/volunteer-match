// Default login illustration (original artwork): Jeju dusk, Hallasan, the sea and a convention-centre building.
// Replaced by a real photo when an administrator uploads one (Admin > Backup / License / Logo).
const SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMax slice" role="img" aria-label="제주 컨벤션센터 일러스트">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0b2b45"/><stop offset=".55" stop-color="#1c6a86"/><stop offset=".8" stop-color="#f3a45a"/><stop offset="1" stop-color="#ffd9a0"/></linearGradient>
<linearGradient id="sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a8aa3"/><stop offset="1" stop-color="#0d3f5c"/></linearGradient>
<linearGradient id="glass" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#bfe6f2"/><stop offset="1" stop-color="#3f93b3"/></linearGradient>
<linearGradient id="roof" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#f6f9fb"/><stop offset="1" stop-color="#cfdbe3"/></linearGradient>
<radialGradient id="sun" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff3c9"/><stop offset="1" stop-color="#fff3c9" stop-opacity="0"/></radialGradient>
</defs>
<rect width="1200" height="800" fill="url(#sky)"/>
<circle cx="880" cy="430" r="190" fill="url(#sun)"/><circle cx="880" cy="440" r="46" fill="#fff0c2"/>
<g fill="#fff" opacity=".18"><ellipse cx="260" cy="170" rx="150" ry="22"/><ellipse cx="380" cy="205" rx="110" ry="16"/><ellipse cx="980" cy="130" rx="130" ry="18"/></g>
<path d="M0 560 C140 520 230 470 330 430 C420 395 470 350 560 345 C650 350 700 395 800 440 C900 482 1020 520 1200 548 L1200 640 L0 640Z" fill="#17506a" opacity=".85"/>
<path d="M0 590 C200 560 360 540 520 565 C700 592 900 560 1200 585 L1200 650 L0 650Z" fill="#1f6b84" opacity=".9"/>
<rect y="620" width="1200" height="180" fill="url(#sea)"/>
<g stroke="#bfe6f2" stroke-opacity=".35" stroke-width="2" fill="none"><path d="M40 680 q40 -12 80 0 t80 0 t80 0"/><path d="M360 710 q50 -14 100 0 t100 0 t100 0"/><path d="M820 690 q45 -12 90 0 t90 0 t90 0"/><path d="M120 760 q60 -14 120 0 t120 0"/><path d="M700 765 q60 -14 120 0 t120 0"/></g>
<path d="M0 700 C180 668 330 676 470 700 L470 800 L0 800Z" fill="#0b3a54"/>
<g transform="translate(404 515) scale(.6)">
<path d="M0 200 Q330 -70 660 200 L660 232 L0 232Z" fill="url(#roof)"/>
<path d="M40 214 Q330 -20 620 214" fill="none" stroke="#9fb4c0" stroke-width="3"/>
<rect x="30" y="232" width="600" height="118" fill="url(#glass)"/>
<g stroke="#eaf6fb" stroke-opacity=".7" stroke-width="3"><path d="M110 232v118M190 232v118M270 232v118M350 232v118M430 232v118M510 232v118M590 232v118"/></g>
<rect x="10" y="350" width="640" height="16" fill="#e8eef2"/>
<rect x="290" y="286" width="80" height="64" fill="#0d3f5c" opacity=".55"/>
<g fill="#fff" opacity=".9"><circle cx="330" cy="96" r="5"/></g>
</g>
<g fill="#0b3a54" transform="translate(300 90)"><path d="M110 640 q-4 -90 16 -150 q6 70 12 150z"/><path d="M126 492 q-60 -6 -86 30 q50 -14 86 -6z M126 492 q56 -14 92 24 q-52 -10 -92 -4z M126 492 q-26 -46 -70 -48 q50 14 70 48z M126 492 q30 -48 82 -42 q-52 10 -82 42z"/></g>
<path d="M0 800 L0 740 C200 722 420 745 640 760 C880 776 1040 748 1200 738 L1200 800Z" fill="#082c43"/>
</svg>`;

export function heroArt() {
  const doc = new DOMParser().parseFromString(SVG, "image/svg+xml");
  const node = document.importNode(doc.documentElement, true);
  node.classList.add("hero-bg");
  return node;
}
