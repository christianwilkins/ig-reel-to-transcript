# 21st.dev + Cursor Prompt Pack

Use this when reel ideas are about websites, UI components, or vibecoding.

## Master prompt

```text
You are implementing production UI in a Next.js + Tailwind + shadcn project.

Goal:
Build <SCREEN/FEATURE> for <AUDIENCE> with one primary user action: <ACTION>.

Constraints:
- TypeScript only
- Tailwind utility classes only
- No inline styles
- Mobile first
- Accessibility: WCAG AA, visible focus ring, min 40px touch targets

Component sourcing:
- Use 21st.dev component patterns
- Cite the exact 21st.dev component slugs/links you used
- If no exact match, compose from 2-3 related 21st components

Deliverables:
1) Three UI variants (A/B/C) with brief pros/cons
2) Choose one final variant and implement full code
3) Explain spacing, hierarchy, and interaction decisions
4) Include test checklist for responsive and keyboard navigation

Quality gate:
Score final UI 0-20 on each:
- hierarchy clarity
- readability
- interaction friction
- accessibility
- visual consistency
Include total score out of 100.
```

## Fast command prompt

```text
/ui Build a modern <component> for <use-case>. Use 21st.dev style patterns, 3 variants, then ship best variant with accessibility checks.
```

## Review prompt

```text
Audit this component for UX debt and rewrite it. Keep behavior, improve hierarchy, spacing, and accessibility. Return a clean diff and explain changes in plain language.
```
