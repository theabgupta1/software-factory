# Design playbook

How factory agents design, build and review anything a user sees, so the result is modern, distinctive and
polished rather than a stock template. It belongs to the contracts of the builder, frontend engineer, UX
reviewer and UI reviewer. The factory protocol still wins on safety, files and evidence.

## 1. Pick the mode first

- **Extend** (the default when the app already has a design system: a token or theme file, a component library,
  established screens). Stay inside it and raise the craft within it: hierarchy, rhythm, states,
  micro-interactions and motion that use the system's own values. A missing value becomes a new token in the
  token source, with a reason; never a one-off.
- **Direction** when there is no design system, or the request, spec, rework note or a user message asks for a
  new look, a redesign, a landing or marketing page, "modern", "premium" or "delightful", motion, or 3D. Set a
  direction (section 3) and implement it as tokens before any screen.

Write the mode and the reason in your Output file. When the signals conflict, follow the spec and say so.

## 2. Research references (both modes; about 15 minutes, never more than 25)

Look at how the best products solve the same job before you design or judge it.

| For | Where |
|---|---|
| Real, shipped flows and screens | Mobbin, Page Flows, App Store and Google Play screenshots, the leading products' public sites and docs |
| Visual direction and mood | Dribbble, Behance, Awwwards, Godly, Land-book, Lapa Ninja, Refero, Siteinspire |
| Craft details and platform rules | Apple Human Interface Guidelines, Material 3, Radix and shadcn/ui patterns, WAI-ARIA Authoring Practices |

- Search with the domain and the screen, for example `fintech onboarding flow site:mobbin.com`,
  `analytics dashboard dark site:dribbble.com`, `pricing page awwwards`. Open public pages with WebFetch, or in
  your own `agent-browser` session (read-only: no sign-in, no forms) and screenshot them into the temp
  directory to look at them. Mobbin and some galleries need an account: use what search results and public
  pages show, and never sign in.
- Choose 3 to 5 references. For each, write one line: what you borrow (a pattern, a layout idea, a motion idea,
  a type pairing) and why it fits this product.
- Prefer shipped products for flows and behaviour; many Dribbble shots are concepts that ignore real content,
  states and accessibility, so use them for mood, not for structure.
- Borrow ideas, never artefacts: no copied code, images, illustrations, icons, fonts or 3D models. Use the
  repo's assets, open-licence fonts (Google Fonts, Fontsource, system stacks) and CC0 assets with the source
  recorded.

## 3. Write the design direction (Direction mode)

In your Output file, under `## Design direction`, keep it to about 20 lines:

- **Subject, audience and primary job**, and the tone in three words (for example "calm, precise, warm").
- **References** and what each contributes.
- **Type**: one or two families with clearly different roles; a modular scale (ratio 1.2 to 1.333); weights;
  line height (1.4 to 1.6 for body, tighter for display); body lines of 45 to 75 characters.
- **Colour**: 4 to 6 named core values and their semantic roles (background, surface, text, muted text,
  accent, success, warning, danger), for light and dark when the app has both, with contrast checked.
- **Space and shape**: a 4 or 8 px spacing scale; a radius scale where nested radii stay concentric (child
  radius = parent radius minus padding); elevation as layered shadows tinted towards the background hue.
- **Layout**: the grid, the density, and the one memorable element of the screen. Keep everything around it
  quiet and disciplined.
- **Motion language** (section 5) and **3D** (section 6, only when asked).

Then review the direction against the brief: name anything generic (section 4's tells), revise it, and say what
you changed and why. Implement it as tokens (CSS custom properties, the theme file, Tailwind config, platform
theme) before building screens.

## 4. The craft bar (both modes)

- **Hierarchy**: one primary action per view; a clear headline; secondary actions visibly secondary.
- **Rhythm and alignment**: every space from the scale; every element aligned to the grid, a baseline or an
  edge; adjust by 1 px where optical alignment beats geometry (icons beside text, play triangles).
- **Typography**: tabular numerals for figures that are compared; no widows in headings; real ellipsis and
  curly quotes; never below 16 px for inputs on mobile.
- **Colour**: status always paired with text or an icon; hover, active and focus states with more contrast
  than rest; borders and shadows tinted, not pure grey; dark theme sets `color-scheme: dark`.
- **States**: hover, `:focus-visible`, active, disabled, loading (the button keeps its label), empty (invites
  the next action), error (says what happened and how to recover, keeps the user's input), success; skeletons
  match the final content's size.
- **Resilience**: very long, very short, missing and localised content; 375, 768, 1280 and 1440+ px widths;
  safe-area insets; no horizontal page scroll; touch targets at least 44 px on touch screens.
- **Writing**: from the user's side, plain, consistent ("Publish" leads to "Published"); errors without blame
  or apology; no lorem ipsum.

**Stock tells** to avoid unless the brief truly calls for them: the framework's default theme left untouched;
one sans-serif everywhere with no type scale; a grid of identical rounded cards with the same soft shadow;
purple-to-blue gradients on white; "big number, small label, gradient accent" stat tiles; fade-and-slide-up on
every section; a hover lift on every card; emoji as icons; everything centred; tracked all-caps eyebrows above
every heading; an arrow on every link; generic stock photography.

## 5. Motion-based UX

Motion must explain something: feedback (a press registered, a save succeeded), orientation (where a panel
came from and went), continuity (a shared element, a reordered list), or attention for one orchestrated moment.
Delight is welcome when it is rare and fast.

- **Durations**: 100 to 200 ms for micro-interactions, 200 to 300 ms for component transitions, 300 to 500 ms
  for page-level or large moves; exits faster than entrances; nothing on a task's critical path above 500 ms.
- **Easing**: decelerate (ease-out) to enter, accelerate (ease-in) to exit, ease-in-out to move; springs for
  direct manipulation and for motion that must be interruptible; keep overshoot subtle in serious products.
- **Mechanics**: animate `transform` and `opacity` only; never `transition: all`; never animate width, height,
  top or left; use FLIP or the View Transitions API for layout changes; anchor motion to its origin (a menu
  grows from its button); stagger lists by 20 to 50 ms per item with a capped total; user input interrupts and
  retargets any animation.
- **Perceived speed**: optimistic updates with undo or rollback; show a spinner only after about 150 to 300 ms
  and keep it long enough not to flicker; skeletons over spinners for content.
- **Reduced motion**: under `prefers-reduced-motion: reduce`, replace movement with opacity changes or none;
  no parallax, auto-play or auto-rotation; anything that moves for more than 5 seconds has pause controls.
- **Tokens**: define motion tokens beside the design tokens (for example `--duration-fast`, `--duration-base`,
  `--ease-standard`, `--ease-emphasised`) and use only those.
- **Tools, by what the repo already has**: CSS transitions and keyframes, and the View Transitions API, first;
  Motion (formerly Framer Motion) in React; GSAP for complex timelines and scroll-linked work (check its licence
  terms for this use); Lottie or Rive for illustrative animation; Reanimated in React Native; SwiftUI and
  Jetpack Compose animation APIs on native. Add a dependency only when CSS cannot do the job, and say why.

## 6. 3D (only when the request, spec or a user message asks for it)

- **Stack**: Three.js; React Three Fiber with drei in React apps; `<model-viewer>` for showing a product model;
  a Spline export when the user supplies a scene; Babylon.js when the repo already uses it.
- **Assets**: glTF or GLB, compressed (Draco or Meshopt geometry, KTX2 textures); budgets of about 2 MB
  transferred for a hero scene, under about 100 draw calls, textures up to 2048 px; only CC0 or properly
  licensed models (for example Poly Haven, Kenney) or the user's own, with the source recorded.
- **Look**: physically based materials, an environment map and soft contact shadows; colours from the palette;
  never the default grey lighting.
- **Performance**: load the canvas after the main content (lazy, code-split); render on demand when the scene
  is static; cap the device pixel ratio at 2; pause when off-screen or the tab is hidden; dispose geometries
  and textures on unmount; check at 375 px width.
- **Fallbacks and access**: a poster image while loading and when WebGL is unavailable; a decorative canvas is
  `aria-hidden`; meaningful 3D has a text or 2D alternative; interactive 3D works with the keyboard; reduced
  motion stops auto-rotation.

## 7. Accessibility floor (WCAG 2.2 AA)

Contrast 4.5:1 for body text and 3:1 for large text and UI components; visible focus that sticky headers never
cover; targets at least 24 by 24 CSS px; every action by keyboard and every drag with a click alternative;
labels tied to inputs and errors announced; nothing conveyed by colour alone; content reflows at 320 px and
zooms to 200 %; one heading hierarchy and a skip link; status updates announced politely; prefer native HTML
over ARIA.

## 8. Performance budgets

At the 75th percentile: LCP under 2.5 s, INP under 200 ms, CLS under 0.1. Images carry width and height, use
modern formats and lazy-load below the fold. At most two font families, subset, with `font-display: swap` and
the critical one preloaded. No heavy dependency for a small need; code-split routes, 3D and charts; virtualise
long lists.

## 9. Critique loop (builders, before you finish)

1. Screenshot each changed view at 375 and 1280 px, in both themes when the app has them.
2. Put them next to your references. Write down the three weakest things you see.
3. Fix the biggest one. Then remove one thing that does not earn its place.
4. Screenshot again and record before and after, with paths in the temp directory, in your Output file.

## 10. What reviewers block on

- **UI reviewer**, `VERDICT: CHANGES` for:
  - off-system values where a token exists;
  - layout broken at a supported width;
  - contrast failures;
  - missing interaction states;
  - motion that animates layout properties, ignores reduced motion or blocks input;
  - 3D without a fallback or over budget;
  - in Direction mode, no written direction, or a result where section 4's tells dominate the screen.

  Each finding comes with a concrete fix. Taste beyond that goes under Notes.
- **UX reviewer**, `VERDICT: CHANGES` for:
  - dead ends;
  - missing states;
  - no feedback after an action;
  - motion that hides state or slows a task;
  - accessibility blockers;
  - a flow clearly worse than the best-in-class pattern for the same job (more steps, lost input, unclear
    next action), named with the reference and the specific fix.
