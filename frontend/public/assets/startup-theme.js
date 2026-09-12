// Runs before the initial stylesheet; external so Web and Tauri CSP can keep script-src 'self'.
try {
  const theme = localStorage.getItem('cursor.v2.theme')
  document.documentElement.dataset.theme = theme === 'light' || (theme === 'system' && !matchMedia('(prefers-color-scheme: dark)').matches) ? 'light' : 'dark'
} catch { /* Keep the document's default when storage is unavailable. */ }
