// D1 is optional in the local console; getDb checks the binding before use.
declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
  }
}
