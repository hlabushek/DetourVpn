import * as sdk from 'sdk';

export default async function (_input, ctx) {
  const globalNames = Object.getOwnPropertyNames(globalThis);
  const contextNames = ctx && typeof ctx === 'object' ? Object.getOwnPropertyNames(ctx) : [];
  const sdkNames = Object.keys(sdk);

  return {
    hasProcessGlobal: globalNames.includes('process'),
    hasEnvGlobal: globalNames.includes('env'),
    hasDenoGlobal: globalNames.includes('Deno'),
    hasBunGlobal: globalNames.includes('Bun'),
    hasSecretContextField: contextNames.some((name) => /secret|env/i.test(name)),
    hasSecretSdkExport: sdkNames.some((name) => /secret|env/i.test(name)),
  };
}
