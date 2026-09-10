/** Stop only a fixture process started by this test, including Windows children. */
import { execFileSync } from 'node:child_process'
import { once } from 'node:events'
import { setTimeout as delay } from 'node:timers/promises'

export async function stopFixture(child) {
  const running = () => child.exitCode === null && child.signalCode === null
  child.stdin?.end()
  if (running()) await Promise.race([once(child, 'exit'), delay(15000, undefined, { ref: false })])
  if (running()) {
    if (process.platform === 'win32') {
      execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', timeout: 15000 })
    } else child.kill('SIGKILL')
    if (running()) await Promise.race([once(child, 'exit'), delay(5000, undefined, { ref: false })])
  }
  child.stdout?.destroy()
  child.stderr?.destroy()
  if (running()) throw new Error('Owned fixture process did not exit')
}
