import base from "../vite.config";
import path from "node:path";
export default {...base,plugins:[...(base.plugins??[]),{name:"owned-live-feedback-composer",enforce:"pre" as const,resolveId(source:string,importer?:string){if(source==="../lib/composer"&&importer?.replaceAll("\\","/").endsWith("/SharedAttachments.tsx"))return path.resolve(import.meta.dirname,"live-feedback-composer.tsx");}}]};
