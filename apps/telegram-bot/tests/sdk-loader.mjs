export async function resolve(specifier, context, next) {
  if (specifier === 'sdk' || specifier === 'sdk/db') {
    return { url: new URL('./sdk-test-double.mjs', import.meta.url).href, shortCircuit: true };
  }
  return next(specifier, context);
}
