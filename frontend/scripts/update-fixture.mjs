import { readFileSync } from 'node:fs'

const version = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version
export const currentRelease = { current_version: version, latest_version: version, available: false, installable: false,
  notes: '', release_url: 'https://github.com/devilcoolyue/cursor-dashboard/releases', published_at: null }

/** Keep automatic checks in unrelated browser fixtures offline and deterministic. */
export async function fixtureContext(browser, options) {
  const context = await browser.newContext(options)
  await context.route('**/api/v1/updates', route => route.fulfill({ json: currentRelease }))
  return context
}
