// Standard gzip retains HTTP compatibility without Zopfli's long build time.
import { Transform } from "node:stream";
import { gzipSync } from "node:zlib";

export default function gzip() {
  return new Transform({
    objectMode: true,
    transform(file, _encoding, callback) {
      if (file.isNull()) {
        callback();
        return;
      }
      try {
        const compressed = gzipSync(file.contents, { level: 9 });
        if (compressed.length < file.contents.length) {
          file.contents = compressed;
          file.path += ".gz";
          callback(null, file);
        } else {
          callback();
        }
      } catch (error) {
        callback(error);
      }
    },
  });
}
