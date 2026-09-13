/** Exercise the same public combobox/listbox interaction as mouse and touch users. */
export async function selectOption(page, name, label) {
  await page.getByRole('combobox', { name, exact: true }).click()
  await page.getByRole('listbox', { name, exact: true }).getByRole('option', { name: label, exact: true }).click()
  await page.getByRole('listbox', { name, exact: true }).waitFor({ state: 'hidden' })
}

export async function reloadList(page) {
  await page.getByRole('button', { name: '重载列表', exact: true }).click()
  await page.getByRole('button', { name: '立即刷新列表', exact: true }).click()
}
