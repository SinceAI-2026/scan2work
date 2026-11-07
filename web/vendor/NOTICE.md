# Bundled browser dependencies

- `three/three.module.js` and `three/addons/controls/OrbitControls.js`: Three.js revision 170, copyright 2010–2024 Three.js authors, MIT. The complete [license](three/LICENSE) is copied from the [r170 release](https://github.com/mrdoob/three.js/blob/r170/LICENSE).
- SVG paths embedded in `../view-icons.mjs`: Hugeicons free Stroke Rounded icons, distributed through Iconify. MIT. The complete [license](hugeicons/LICENSE.md) is copied from the publisher's [`@hugeicons/core-free-icons` 4.4.0 package](https://unpkg.com/@hugeicons/core-free-icons@4.4.0/LICENSE.md). This identifies the source of the notice; no new icon package or renderer was added. [Publisher licensing information](https://hugeicons.com/docs/faqs/license-questions).

The viewer uses system fonts. No font files, icon web fonts, or font CDN are bundled or requested. The optional online ElevenLabs widget is not included in the offline distribution and is not loaded when the manifest explicitly disables external models.
