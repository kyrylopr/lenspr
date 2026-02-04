#!/usr/bin/env node
/**
 * TypeScript Resolver using TypeScript Compiler API
 *
 * This script provides full type inference for resolving names in TypeScript/JavaScript.
 * It loads the project's tsconfig.json and uses the TypeScript type checker.
 *
 * Usage:
 *   Batch mode (stdin/stdout):
 *     echo '{"requests": [...]}' | node ts_resolver.js /path/to/project
 *
 *   Request format:
 *     {"requests": [
 *       {"id": "1", "file": "src/App.tsx", "line": 10, "column": 5},
 *       {"id": "2", "file": "src/utils.ts", "line": 20, "column": 10}
 *     ]}
 *
 *   Response format:
 *     {"results": [
 *       {"id": "1", "nodeId": "src/components/Button.Button", "confidence": "resolved"},
 *       {"id": "2", "nodeId": "react.useState", "confidence": "external"}
 *     ]}
 */

const ts = require('typescript');
const path = require('path');
const fs = require('fs');

// Confidence levels matching Python's EdgeConfidence
const Confidence = {
    RESOLVED: 'resolved',
    INFERRED: 'inferred',
    EXTERNAL: 'external',
    UNRESOLVED: 'unresolved'
};

// Known external packages
const EXTERNAL_PACKAGES = new Set([
    'react', 'react-dom', 'next', 'vue', 'angular', 'express',
    'lodash', 'axios', 'moment', 'dayjs', 'date-fns', 'uuid',
    'zod', 'yup', '@tanstack/react-query', 'swr', 'zustand',
    'redux', '@reduxjs/toolkit', 'mobx', 'immer', 'ramda'
]);

class TypeScriptResolver {
    constructor(projectRoot) {
        this.projectRoot = path.resolve(projectRoot);
        this.program = null;
        this.typeChecker = null;
        this.sourceFiles = new Map();

        this._initializeProgram();
    }

    _initializeProgram() {
        // Find all config files (tsconfig.json, jsconfig.json) in project
        const configPaths = this._findConfigFiles(this.projectRoot);

        let compilerOptions = {
            target: ts.ScriptTarget.ESNext,
            module: ts.ModuleKind.ESNext,
            moduleResolution: ts.ModuleResolutionKind.NodeJs,
            jsx: ts.JsxEmit.React,
            esModuleInterop: true,
            allowJs: true,
            checkJs: true,
            strict: false, // Don't need strict for resolution
            skipLibCheck: true,
            noEmit: true
        };

        let fileNames = [];

        // Process each config file found
        for (const configPath of configPaths) {
            const configFile = ts.readConfigFile(configPath, ts.sys.readFile);
            if (!configFile.error) {
                const parsed = ts.parseJsonConfigFileContent(
                    configFile.config,
                    ts.sys,
                    path.dirname(configPath)
                );
                // Merge options (later configs override)
                compilerOptions = { ...compilerOptions, ...parsed.options };
                // Collect all file names
                fileNames.push(...parsed.fileNames);
            }
        }

        // Deduplicate file names
        fileNames = [...new Set(fileNames)];

        // If no files from configs, find them manually
        if (fileNames.length === 0) {
            fileNames = this._findSourceFiles(this.projectRoot);
        }

        // Create the program
        this.program = ts.createProgram(fileNames, compilerOptions);
        this.typeChecker = this.program.getTypeChecker();

        // Cache source files
        for (const sourceFile of this.program.getSourceFiles()) {
            if (!sourceFile.isDeclarationFile) {
                const relativePath = path.relative(this.projectRoot, sourceFile.fileName);
                if (!relativePath.startsWith('node_modules')) {
                    this.sourceFiles.set(relativePath, sourceFile);
                    // Also store by absolute path
                    this.sourceFiles.set(sourceFile.fileName, sourceFile);
                }
            }
        }
    }

    _findConfigFiles(dir) {
        const configs = [];
        const configNames = ['tsconfig.json', 'jsconfig.json'];
        const skipDirs = new Set([
            'node_modules', '.git', 'dist', 'build', '.next',
            'coverage', '.cache', '__pycache__', '.lens'
        ]);

        const walk = (currentDir) => {
            try {
                const entries = fs.readdirSync(currentDir, { withFileTypes: true });
                for (const entry of entries) {
                    const fullPath = path.join(currentDir, entry.name);
                    if (entry.isDirectory()) {
                        if (!skipDirs.has(entry.name) && !entry.name.startsWith('.')) {
                            walk(fullPath);
                        }
                    } else if (entry.isFile() && configNames.includes(entry.name)) {
                        configs.push(fullPath);
                    }
                }
            } catch (e) {
                // Skip directories we can't read
            }
        };

        walk(dir);
        return configs;
    }

    _findSourceFiles(dir) {
        const files = [];
        const extensions = ['.ts', '.tsx', '.js', '.jsx'];
        const skipDirs = new Set([
            'node_modules', '.git', 'dist', 'build', '.next',
            'coverage', '.cache', '__pycache__'
        ]);

        const walk = (currentDir) => {
            try {
                const entries = fs.readdirSync(currentDir, { withFileTypes: true });
                for (const entry of entries) {
                    const fullPath = path.join(currentDir, entry.name);
                    if (entry.isDirectory()) {
                        if (!skipDirs.has(entry.name) && !entry.name.startsWith('.')) {
                            walk(fullPath);
                        }
                    } else if (entry.isFile()) {
                        const ext = path.extname(entry.name);
                        if (extensions.includes(ext)) {
                            files.push(fullPath);
                        }
                    }
                }
            } catch (e) {
                // Skip directories we can't read
            }
        };

        walk(dir);
        return files;
    }

    /**
     * Resolve a name at a specific position in a file.
     *
     * @param {string} filePath - Relative or absolute path to the file
     * @param {number} line - 1-based line number
     * @param {number} column - 0-based column number
     * @returns {Object} Resolution result with nodeId and confidence
     */
    resolve(filePath, line, column) {
        // Get the source file
        let sourceFile = this.sourceFiles.get(filePath);
        if (!sourceFile) {
            // Try absolute path
            const absolutePath = path.resolve(this.projectRoot, filePath);
            sourceFile = this.sourceFiles.get(absolutePath);
        }
        if (!sourceFile) {
            return {
                nodeId: null,
                confidence: Confidence.UNRESOLVED,
                reason: 'file_not_found'
            };
        }

        // Convert line/column to position
        const position = this._getPositionOfLineAndCharacter(sourceFile, line - 1, column);
        if (position < 0) {
            return {
                nodeId: null,
                confidence: Confidence.UNRESOLVED,
                reason: 'invalid_position'
            };
        }

        // Find the node at this position
        const node = this._findNodeAtPosition(sourceFile, position);
        if (!node) {
            return {
                nodeId: null,
                confidence: Confidence.UNRESOLVED,
                reason: 'no_node_at_position'
            };
        }

        // Try to resolve the node
        return this._resolveNode(node, sourceFile);
    }

    _getPositionOfLineAndCharacter(sourceFile, line, character) {
        try {
            return sourceFile.getPositionOfLineAndCharacter(line, character);
        } catch (e) {
            // Fall back to manual calculation
            const lines = sourceFile.text.split('\n');
            let position = 0;
            for (let i = 0; i < line && i < lines.length; i++) {
                position += lines[i].length + 1; // +1 for newline
            }
            return position + character;
        }
    }

    _findNodeAtPosition(sourceFile, position) {
        let result = null;

        const visit = (node) => {
            if (position >= node.getStart() && position < node.getEnd()) {
                result = node;
                ts.forEachChild(node, visit);
            }
        };

        visit(sourceFile);
        return result;
    }

    _resolveNode(node, sourceFile) {
        // Handle identifiers
        if (ts.isIdentifier(node)) {
            return this._resolveIdentifier(node, sourceFile);
        }

        // Handle property access (e.g., obj.method)
        if (ts.isPropertyAccessExpression(node)) {
            return this._resolvePropertyAccess(node, sourceFile);
        }

        // Handle call expressions
        if (ts.isCallExpression(node)) {
            return this._resolveNode(node.expression, sourceFile);
        }

        // Handle JSX elements
        if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
            const tagName = node.tagName;
            if (ts.isIdentifier(tagName)) {
                return this._resolveIdentifier(tagName, sourceFile);
            }
            // Handle namespaced JSX like <Foo.Bar />
            if (ts.isPropertyAccessExpression(tagName)) {
                return this._resolvePropertyAccess(tagName, sourceFile);
            }
        }

        // Phase 3: Handle class heritage (extends clause)
        if (ts.isHeritageClause(node)) {
            // For `extends BaseClass`, resolve the base class
            if (node.token === ts.SyntaxKind.ExtendsKeyword && node.types && node.types.length > 0) {
                const extendsExpr = node.types[0].expression;
                return this._resolveNode(extendsExpr, sourceFile);
            }
        }

        // Handle expression with type arguments (BaseClass<T>)
        if (ts.isExpressionWithTypeArguments(node)) {
            return this._resolveNode(node.expression, sourceFile);
        }

        return {
            nodeId: null,
            confidence: Confidence.UNRESOLVED,
            reason: 'unsupported_node_type'
        };
    }

    _resolveIdentifier(node, sourceFile) {
        const name = node.text;

        // Get the symbol for this identifier
        const symbol = this.typeChecker.getSymbolAtLocation(node);
        if (!symbol) {
            return {
                nodeId: name,
                confidence: Confidence.INFERRED,
                reason: 'no_symbol'
            };
        }

        // Check if this is a default import
        const isDefault = this._isDefaultImport(node);

        // Follow aliases (imports) with improved re-export chain following
        const resolvedSymbol = this._followReexportChain(symbol);
        if (!resolvedSymbol) {
            return {
                nodeId: name,
                confidence: Confidence.INFERRED,
                reason: 'cannot_resolve_symbol'
            };
        }

        // Get the declaration
        const declarations = resolvedSymbol.getDeclarations();
        if (!declarations || declarations.length === 0) {
            return {
                nodeId: name,
                confidence: Confidence.INFERRED,
                reason: 'no_declarations'
            };
        }

        // For default imports, prefer the default export declaration
        let declaration = declarations[0];
        if (isDefault && declarations.length > 1) {
            const defaultDecl = declarations.find(d => this._isDefaultExportDeclaration(d));
            if (defaultDecl) {
                declaration = defaultDecl;
            }
        }

        const declarationFile = declaration.getSourceFile();

        // Check if it's from an external package
        const filePath = declarationFile.fileName;
        if (filePath.includes('node_modules')) {
            const packageName = this._extractPackageName(filePath);
            if (packageName) {
                return {
                    nodeId: `${packageName}.${name}`,
                    confidence: Confidence.EXTERNAL,
                    reason: 'external_package'
                };
            }
        }

        // Check if it's a type definition file
        if (declarationFile.isDeclarationFile) {
            return {
                nodeId: name,
                confidence: Confidence.EXTERNAL,
                reason: 'declaration_file'
            };
        }

        // Build the node ID
        const nodeId = this._buildNodeId(declaration, resolvedSymbol.name);
        return {
            nodeId,
            confidence: Confidence.RESOLVED,
            reason: 'resolved'
        };
    }

    /**
     * Check if this identifier is from a default import.
     * e.g., `import Button from './Button'`
     */
    _isDefaultImport(node) {
        let parent = node.parent;
        while (parent) {
            if (ts.isImportClause(parent)) {
                // This is `import X from '...'` where X is the default
                return parent.name === node;
            }
            parent = parent.parent;
        }
        return false;
    }

    /**
     * Check if a declaration is a default export.
     */
    _isDefaultExportDeclaration(declaration) {
        // Check for `export default`
        if (declaration.modifiers) {
            const hasExport = declaration.modifiers.some(m => m.kind === ts.SyntaxKind.ExportKeyword);
            const hasDefault = declaration.modifiers.some(m => m.kind === ts.SyntaxKind.DefaultKeyword);
            if (hasExport && hasDefault) {
                return true;
            }
        }

        // Check if it's an export assignment (export default X or export = X)
        if (ts.isExportAssignment(declaration)) {
            return !declaration.isExportEquals; // ES6 default, not CommonJS
        }

        return false;
    }

    _resolvePropertyAccess(node, sourceFile) {
        const propertyName = node.name.text;

        // Get the type of the expression being accessed
        const expressionType = this.typeChecker.getTypeAtLocation(node.expression);
        const symbol = expressionType.getSymbol();

        // Try to resolve the property via symbol lookup first
        const propSymbol = this.typeChecker.getSymbolAtLocation(node.name);
        if (propSymbol) {
            const resolved = this._followReexportChain(propSymbol);
            if (resolved) {
                const declarations = resolved.getDeclarations();
                if (declarations && declarations.length > 0) {
                    const declaration = declarations[0];
                    const declarationFile = declaration.getSourceFile();

                    // Check if external
                    if (declarationFile.fileName.includes('node_modules')) {
                        const packageName = this._extractPackageName(declarationFile.fileName);
                        return {
                            nodeId: `${packageName}.${propertyName}`,
                            confidence: Confidence.EXTERNAL
                        };
                    }

                    if (!declarationFile.isDeclarationFile) {
                        // Build node ID
                        const nodeId = this._buildNodeId(declaration, propertyName);
                        return {
                            nodeId,
                            confidence: Confidence.RESOLVED
                        };
                    }
                }
            }
        }

        // Phase 4: Try to resolve via apparent properties (type enumeration)
        if (expressionType) {
            try {
                const apparentProps = this.typeChecker.getApparentProperties(expressionType);
                const matchingProp = apparentProps.find(p => p.name === propertyName);

                if (matchingProp) {
                    const resolved = this._followReexportChain(matchingProp);
                    if (resolved) {
                        const declarations = resolved.getDeclarations();
                        if (declarations && declarations.length > 0) {
                            const declaration = declarations[0];
                            const declarationFile = declaration.getSourceFile();

                            // Check if external
                            if (declarationFile.fileName.includes('node_modules')) {
                                const packageName = this._extractPackageName(declarationFile.fileName);
                                return {
                                    nodeId: `${packageName}.${propertyName}`,
                                    confidence: Confidence.EXTERNAL
                                };
                            }

                            if (!declarationFile.isDeclarationFile) {
                                const nodeId = this._buildNodeId(declaration, propertyName);
                                return {
                                    nodeId,
                                    confidence: Confidence.RESOLVED
                                };
                            }
                        }
                    }
                }
            } catch (e) {
                // getApparentProperties can fail for some types
            }
        }

        // Fall back to type-based resolution
        if (symbol) {
            const symbolName = symbol.name;
            return {
                nodeId: `${symbolName}.${propertyName}`,
                confidence: Confidence.INFERRED,
                reason: 'type_based'
            };
        }

        return {
            nodeId: propertyName,
            confidence: Confidence.INFERRED,
            reason: 'cannot_resolve_property'
        };
    }

    /**
     * Follow re-export chains to find the actual declaration.
     * Handles: export { X } from './module', export * from './module', etc.
     * @param {ts.Symbol} symbol - The symbol to resolve
     * @param {number} maxDepth - Maximum chain depth to prevent infinite loops
     * @returns {ts.Symbol|null} The resolved symbol
     */
    _followReexportChain(symbol, maxDepth = 15) {
        let current = symbol;
        let depth = 0;
        const seen = new Set();

        while (current && depth < maxDepth) {
            // Use symbol name + declaration location as unique ID
            const symbolKey = this._getSymbolKey(current);
            if (seen.has(symbolKey)) {
                // Circular reference detected
                break;
            }
            seen.add(symbolKey);

            // Check if this is an alias (import or re-export)
            if (current.flags & ts.SymbolFlags.Alias) {
                try {
                    const aliased = this.typeChecker.getAliasedSymbol(current);
                    if (aliased && aliased !== current) {
                        current = aliased;
                        depth++;
                        continue;
                    }
                } catch (e) {
                    // getAliasedSymbol can throw for some edge cases
                    break;
                }
            }

            // Check for export value (module.exports = X)
            if (current.flags & ts.SymbolFlags.ExportValue) {
                try {
                    const exported = this.typeChecker.getExportSymbolOfSymbol?.(current);
                    if (exported && exported !== current) {
                        current = exported;
                        depth++;
                        continue;
                    }
                } catch (e) {
                    break;
                }
            }

            // No more chain to follow
            break;
        }

        return current;
    }

    /**
     * Get a unique key for a symbol to detect circular references.
     */
    _getSymbolKey(symbol) {
        const declarations = symbol.getDeclarations?.();
        if (declarations && declarations.length > 0) {
            const decl = declarations[0];
            const sf = decl.getSourceFile();
            return `${symbol.name}@${sf.fileName}:${decl.getStart()}`;
        }
        return `${symbol.name}@unknown`;
    }

    /**
     * Legacy method for backwards compatibility.
     * @deprecated Use _followReexportChain instead
     */
    _resolveSymbol(symbol) {
        return this._followReexportChain(symbol);
    }

    _extractPackageName(filePath) {
        const nodeModulesIndex = filePath.lastIndexOf('node_modules');
        if (nodeModulesIndex === -1) return null;

        const afterNodeModules = filePath.substring(nodeModulesIndex + 'node_modules/'.length);
        const parts = afterNodeModules.split('/');

        // Handle scoped packages (@org/package)
        if (parts[0].startsWith('@') && parts.length > 1) {
            return `${parts[0]}/${parts[1]}`;
        }
        return parts[0];
    }

    _buildNodeId(declaration, name) {
        const sourceFile = declaration.getSourceFile();
        const filePath = sourceFile.fileName;

        // Get relative path from project root
        let relativePath = path.relative(this.projectRoot, filePath);

        // Remove extension
        relativePath = relativePath.replace(/\.(tsx?|jsx?)$/, '');

        // Convert path separators to dots
        let moduleId = relativePath.replace(/[/\\]/g, '.');

        // Handle index files
        if (moduleId.endsWith('.index')) {
            moduleId = moduleId.slice(0, -6);
        }

        // Find the containing function/class/module
        const container = this._findContainer(declaration);
        if (container && container !== name) {
            return `${moduleId}.${container}.${name}`;
        }

        return `${moduleId}.${name}`;
    }

    _findContainer(node) {
        let current = node.parent;
        while (current) {
            if (ts.isFunctionDeclaration(current) && current.name) {
                return current.name.text;
            }
            if (ts.isClassDeclaration(current) && current.name) {
                return current.name.text;
            }
            if (ts.isMethodDeclaration(current) && current.name) {
                if (ts.isIdentifier(current.name)) {
                    // Get class name too
                    const classNode = current.parent?.parent;
                    if (ts.isClassDeclaration(classNode) && classNode.name) {
                        return `${classNode.name.text}.${current.name.text}`;
                    }
                    return current.name.text;
                }
            }
            current = current.parent;
        }
        return null;
    }

    /**
     * Resolve a call at a specific location.
     * This is a higher-level method that handles common patterns.
     */
    resolveCall(filePath, line, column, callName) {
        // First try position-based resolution
        const result = this.resolve(filePath, line, column);
        if (result.confidence === Confidence.RESOLVED ||
            result.confidence === Confidence.EXTERNAL) {
            return result;
        }

        // Check if it's a known external package call
        if (callName && callName.includes('.')) {
            const parts = callName.split('.');
            const packageName = parts[0].toLowerCase();
            if (EXTERNAL_PACKAGES.has(packageName)) {
                return {
                    nodeId: callName,
                    confidence: Confidence.EXTERNAL,
                    reason: 'known_external_package'
                };
            }
        }

        return result;
    }

    /**
     * Process multiple requests in batch.
     */
    processBatch(requests) {
        const results = [];
        for (const req of requests) {
            const result = this.resolve(req.file, req.line, req.column);
            results.push({
                id: req.id,
                ...result
            });
        }
        return results;
    }

    /**
     * Get resolver statistics.
     */
    getStats() {
        return {
            projectRoot: this.projectRoot,
            sourceFileCount: this.sourceFiles.size,
            hasTypeChecker: this.typeChecker !== null
        };
    }
}

// Main entry point
function main() {
    const args = process.argv.slice(2);

    if (args.length === 0) {
        console.error('Usage: node ts_resolver.js <project_root>');
        console.error('       Reads JSON requests from stdin, writes results to stdout');
        process.exit(1);
    }

    const projectRoot = args[0];

    // Check if project exists
    if (!fs.existsSync(projectRoot)) {
        console.error(JSON.stringify({
            error: `Project root does not exist: ${projectRoot}`
        }));
        process.exit(1);
    }

    // Initialize resolver
    let resolver;
    try {
        resolver = new TypeScriptResolver(projectRoot);
    } catch (e) {
        console.error(JSON.stringify({
            error: `Failed to initialize resolver: ${e.message}`
        }));
        process.exit(1);
    }

    // Read input from stdin
    let input = '';
    process.stdin.setEncoding('utf8');

    process.stdin.on('data', (chunk) => {
        input += chunk;
    });

    process.stdin.on('end', () => {
        try {
            const data = JSON.parse(input);

            if (data.command === 'stats') {
                console.log(JSON.stringify(resolver.getStats()));
                return;
            }

            if (!data.requests || !Array.isArray(data.requests)) {
                console.log(JSON.stringify({
                    error: 'Invalid input: expected {"requests": [...]}'
                }));
                return;
            }

            const results = resolver.processBatch(data.requests);
            console.log(JSON.stringify({ results }));

        } catch (e) {
            console.log(JSON.stringify({
                error: `Failed to process requests: ${e.message}`
            }));
        }
    });
}

// Export for testing
module.exports = { TypeScriptResolver, Confidence };

// Run if called directly
if (require.main === module) {
    main();
}
