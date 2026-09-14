// Version the request so an already cached, shard-only edition cannot be used
// with the bundled-details client. Keep one coherent snapshot per page load.
export const ATLAS_DATA_URL = 'data/atlas.json.gz?v=math02-training01';

class LoadError extends Error {
  constructor(message, retryable) { super(message); this.retryable = retryable; }
}

export async function loadAtlas({fetchImpl = globalThis.fetch,
  wait = ms => new Promise(resolve => setTimeout(resolve, ms)), timeoutMs = 60000} = {}) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    let failure;
    try {
      const response = await fetchImpl(ATLAS_DATA_URL, {
        credentials: 'same-origin', signal: controller.signal,
        cache: attempt === 0 ? 'default' : 'reload',
      });
      if (response.status === 401 || response.status === 403) {
        throw new LoadError('Access to the atlas could not be confirmed. Reload the page to sign in again.', false);
      }
      if (!response.ok) {
        throw new LoadError('The atlas could not be downloaded. Please reload to try again.',
          response.status === 408 || response.status === 429 || response.status >= 500);
      }
      if (response.headers.get('content-type')?.includes('text/html')) {
        throw new LoadError('The site returned a page instead of graph data. Reload the atlas to try again.', false);
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      // A host may already decode Content-Encoding. Inspect the bytes so a
      // payload is never decompressed twice. The entire graph still loads once.
      if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
        if (typeof DecompressionStream === 'undefined') {
          throw new LoadError('This browser cannot open the compressed atlas. Please use a current browser.', false);
        }
        try {
          const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
          return await new Response(stream).json();
        } catch {
          throw new LoadError('The compressed atlas download was incomplete. Reload to try again.', true);
        }
      }
      return JSON.parse(new TextDecoder().decode(bytes));
    } catch (error) {
      failure = error;
    } finally {
      clearTimeout(timeout);
    }
    const retryable = failure instanceof LoadError ? failure.retryable
      : failure instanceof TypeError || failure instanceof SyntaxError || failure?.name === 'AbortError';
    if (!retryable || attempt === 2) {
      if (failure instanceof LoadError) throw failure;
      throw new Error('The atlas download was interrupted. Check your connection and reload to try again.', {cause: failure});
    }
    await wait(250 * 2 ** attempt);
  }
}
