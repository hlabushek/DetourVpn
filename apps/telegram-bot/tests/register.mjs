// TEST ONLY loader: production SDK modules never fall back to these adapters.
import { register } from 'node:module';
register('./sdk-loader.mjs', import.meta.url);
