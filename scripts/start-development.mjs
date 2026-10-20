/** Run Next with actual TLS when the configured local application origin uses HTTPS. */
import { spawn, execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import nextEnvironment from "@next/env";

process.env.NODE_ENV = "development";
const root = fileURLToPath(new URL("..", import.meta.url));
nextEnvironment.loadEnvConfig(root, true);
const origin = new URL(process.env.CONNECTORS__PUBLIC_URL || "http://localhost:3000");
const args = [resolve(root, "node_modules/next/dist/bin/next"), "dev"];
if (origin.protocol === "https:" && ["localhost", "127.0.0.1"].includes(origin.hostname)) {
  let certificate;
  try {
    certificate = JSON.parse(execFileSync(resolve(root, "myenev/bin/python"), ["-m", "scripts.local_https"], {
      cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 15000,
    }));
  } catch {
    throw new Error("Local HTTPS setup failed. Check myenev and the private .local/https certificate pair.");
  }
  args.push("--hostname", origin.hostname, "--port", origin.port || "443", "--experimental-https",
    "--experimental-https-key", certificate.key, "--experimental-https-cert", certificate.certificate,
    "--experimental-https-ca", certificate.certificate);
  console.log("Local HTTPS uses .local/https/localhost.pem. Accept this development certificate in your browser before signing in.");
}
args.push(...process.argv.slice(2));
const child = spawn(process.execPath, args, { cwd: root, stdio: "inherit", env: process.env });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("error", () => { console.error("Could not start the development server."); process.exitCode = 1; });
child.on("exit", (code, signal) => { process.exitCode = code ?? (signal === "SIGTERM" || signal === "SIGINT" ? 0 : 1); });
