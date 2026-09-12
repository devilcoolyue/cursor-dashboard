// Programmatic focus restoration must not turn a pointer click into a keyboard focus ring.
export let restoringFocus = false
export function restoreFocus(action: () => void) {
  const previous = restoringFocus
  restoringFocus = true
  try { action() } finally { restoringFocus = previous }
}
function pointer() { document.documentElement.classList.remove('keyboard-input') }
function keyboard(event: KeyboardEvent) {
  if (!event.metaKey && !event.ctrlKey && (!event.altKey || event.key === 'Tab') && !['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) {
    document.documentElement.classList.add('keyboard-input')
  }
}
pointer()
document.addEventListener('pointerdown', pointer, true)
document.addEventListener('keydown', keyboard, true)
if (import.meta.hot) import.meta.hot.dispose(() => {
  document.removeEventListener('pointerdown', pointer, true)
  document.removeEventListener('keydown', keyboard, true)
})
