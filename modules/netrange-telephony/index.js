// Native-only module. There is nothing to export on the JS side: the Kotlin
// ModuleDefinition registers itself as "NetRangeTelephony" and lib/cellular.ts
// reads it off NativeModules directly.
//
// This file exists because autolinking resolves the module as a `file:`
// dependency, and a package without an entry point is not a package.
module.exports = {};
