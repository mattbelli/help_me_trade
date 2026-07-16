/**
 * devLog — console.log that compiles to a no-op in release builds. Use for
 * diagnostic ([LA]/[push]/etc.) logging that shouldn't run — or cross the JS
 * bridge — in production. Dev builds log as usual.
 */
export const devLog: (...args: unknown[]) => void = __DEV__
  ? console.log.bind(console)
  : () => {};
