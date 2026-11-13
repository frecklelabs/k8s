// Proves SECRET_KEY decrypts the restored ciphertexts, as Silo's at-rest
// cipher does: HKDF-SHA256 (no salt, info silo/data-encryption/v1) to a
// 32-byte AES-256-GCM key, envelope enc:v1:<base64url(nonce|ciphertext|tag)>,
// with the row identity as additional authenticated data. Prints counts only.
'use strict';
const crypto = require('crypto');
const fs = require('fs');
const master = Buffer.from(process.env.SECRET_KEY || '', 'utf8');
if (master.length < 32) { process.stdout.write('key=invalid\n'); process.exit(1); }
const key = Buffer.from(crypto.hkdfSync('sha256', master, Buffer.alloc(0), 'silo/data-encryption/v1', 32));
let ok = 0, failed = 0, pluginOk = 0, pluginJson = 0;
for (const line of fs.readFileSync(process.argv[2], 'utf8').split('\n')) {
  if (line === '') continue;
  const parts = line.split('\t');
  const kind = parts[0], aad = parts[1], value = parts[2] || '';
  try {
    if (!value.startsWith('enc:v1:')) throw new Error('envelope');
    const sealed = Buffer.from(value.slice(7), 'base64url');
    if (sealed.length < 12 + 16) throw new Error('short');
    const decipher = crypto.createDecipheriv('aes-256-gcm', key, sealed.subarray(0, 12));
    decipher.setAAD(Buffer.from(aad, 'utf8'));
    decipher.setAuthTag(sealed.subarray(sealed.length - 16));
    const plain = Buffer.concat([decipher.update(sealed.subarray(12, sealed.length - 16)), decipher.final()]);
    ok++;
    if (kind === 'plugin') {
      pluginOk++;
      const parsed = JSON.parse(plain.toString('utf8'));
      if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) pluginJson++;
    }
  } catch (e) {
    failed++;
  }
}
process.stdout.write('decrypted=' + ok + ' failed=' + failed + ' plugin_configs=' + pluginOk + ' plugin_json=' + pluginJson + '\n');
process.exit(failed === 0 && ok > 0 && pluginJson === pluginOk ? 0 : 1);
