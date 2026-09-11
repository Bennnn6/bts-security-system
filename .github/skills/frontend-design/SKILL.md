---
name: frontend-design
description: 'Design and refresh existing frontend applications with a distinctive, modern visual system. Use for UI redesigns, dashboard polish, responsive layout cleanup, palette changes, typography, component consistency, and browser-based visual verification.'
argument-hint: '[describe the page, product, or visual direction]'
user-invocable: true
disable-model-invocation: false
---

# Frontend Design

## Outcome

Deliver a working frontend change that is visually coherent, responsive, accessible, and consistent with the existing application. Preserve backend behavior, routes, form actions, data contracts, JavaScript hooks, and user workflows unless the request explicitly changes them.

## When to Use

Use this skill for:

- Modernizing an existing website, dashboard, admin panel, or operational tool.
- Establishing or changing a color palette, typography system, spacing, or component language.
- Fixing inconsistent layouts across templates or pages.
- Making a frontend responsive and verifying it in a browser.
- Reviewing a visual implementation for contrast, overflow, hierarchy, and interaction quality.

## Procedure

### 1. Establish the local design context

1. Identify the concrete visual anchor: the main stylesheet, shared layout, design system, route, component, or failing screenshot.
2. Read only the nearby templates, styles, and scripts needed to understand ownership.
3. Check whether the project uses Bootstrap, Tailwind, a component library, or local conventions. Reuse the existing stack.
4. State one local hypothesis about the visual problem and one cheap check that could disconfirm it.
5. Check for existing agent instructions, brand guidance, and project-specific validation commands.

For an existing app, prefer a shared shell and global stylesheet before editing many individual pages. If a page bypasses the shared shell, decide whether that is intentional before changing it.

### 2. Define an opinionated visual direction

Write a short design brief before broad edits. Include:

- Audience and primary workflow.
- Layout density and information hierarchy.
- Typography pairing and fallback fonts.
- Primary, secondary, surface, text, success, warning, and danger colors.
- Border radius, shadow, spacing, and control dimensions.
- Responsive behavior at desktop and mobile widths.
- The memorable visual detail, if one is appropriate.

Avoid generic purple-on-white defaults, excessive gradients, decorative blobs, oversized marketing hero sections, and card-inside-card layouts. Operational interfaces should prioritize scanning, comparison, predictable navigation, and clear status signals.

### 3. Implement from shared primitives

1. Define CSS variables or theme tokens in the owning stylesheet.
2. Link the stylesheet from the shared layout and add a page title block when templates need page-specific titles.
3. Build or refine shared primitives for page shells, headers, buttons, forms, cards, tables, alerts, badges, and navigation.
4. Move duplicated page-local styles into the shared stylesheet when they represent reusable patterns.
5. Keep page-specific rules scoped to the page and preserve existing element IDs and JavaScript selectors.
6. Make controls use the appropriate visual affordance: icons for tool actions, links for navigation, toggles for binary state, and clear text for important commands.
7. Add accessible label/input associations, visible focus states, adequate color contrast, and meaningful accessible names.
8. Avoid unrelated refactors or backend changes.

### 4. Verify behavior and appearance

After the first substantive edit, run the narrowest available check before continuing. Then:

1. Run the project’s focused tests, type checks, template checks, or lint commands.
2. Start the application with the project’s documented environment.
3. Open the most important page in a browser at desktop and mobile widths.
4. Check for horizontal overflow, clipped text, broken wrapping, unreadable contrast, overlapping controls, empty or broken states, and unexpected console errors.
5. Exercise the primary workflow without changing real data when possible.
6. Capture screenshots when visual comparison is useful.

If a check fails, repair the same slice and rerun that check before expanding scope. Treat an overflow or contrast finding as a real defect, not a cosmetic optional item.

### 5. Final review

Review the changed files for:

- Consistent token usage instead of scattered hardcoded colors.
- Correct contrast on every surface, including buttons placed on dark headers.
- Responsive layouts that stack or scroll intentionally.
- No duplicate flash messages, duplicate navigation, or malformed template structure.
- Labels connected to controls and keyboard-visible focus.
- No route, form, ID, or JavaScript regressions.

Report the changed files, the visual direction, validation results, any environment limitation, and the local URL when a development server was started.

## Completion Criteria

The task is complete only when:

- The requested visual direction is applied across the shared UI surface.
- The primary desktop and mobile views render without horizontal overflow or incoherent overlap.
- The project’s available automated checks pass, or failures are clearly explained.
- No unrelated backend behavior was changed.
- The user can run and inspect the result with a concrete command and URL.

## Example Prompts

- `Modernize the admin dashboard with a restrained blue NOC palette. Keep all routes and IDs intact, then verify desktop and mobile layouts.`
- `Make the existing Flask templates visually consistent. Find the shared layout and stylesheet first, move reusable styles there, and check for mobile overflow.`
- `Review this frontend redesign for contrast, accessibility, duplicate UI, and responsive issues. Fix local defects and rerun the focused checks.`
