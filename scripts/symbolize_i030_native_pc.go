// Command symbolize_i030_native_pc maps tombstone PCs using a receipt-pinned
// beta APK or AAR's embedded Go pclntab. It reads no device logs or secrets.
package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"debug/elf"
	"debug/gosym"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const expectedAARSHA256 = "ebe86db3e0c3f88064049011df82503ba2f56a1dce9b6ab5748f00284e7a3f20"
const expectedBeta18APKSHA256 = "019306c82b748bb8df8d196b6d048bfb1c8f551b25a682fbe9a0180d44b3f3d3"
const expectedBeta19APKSHA256 = "fc1cc4941030acf4b527de2ca971df19c58055d944aca19e14f4123f9962be71"
const maxLibraryBytes = 64 << 20

type result struct {
	Issue           string   `json:"issue"`
	ArtifactKind    string   `json:"artifact_kind"`
	ArtifactMatches bool     `json:"artifact_matches_receipt"`
	ArtifactReceipt string   `json:"artifact_receipt"`
	PCs             []symbol `json:"symbols"`
	RawLogsRead     bool     `json:"raw_logs_read"`
	SecretsRead     bool     `json:"secrets_read"`
}

type symbol struct {
	PC           string `json:"pc"`
	Function     string `json:"function,omitempty"`
	SourceFile   string `json:"source_file,omitempty"`
	Line         int    `json:"line,omitempty"`
	MappingScope string `json:"mapping_scope"`
	Result       string `json:"result"`
}

func main() {
	if len(os.Args) < 3 {
		fail("usage")
	}
	var data []byte
	var err error
	if os.Args[1] == "-apk" {
		data, err = io.ReadAll(io.LimitReader(os.Stdin, 512<<20))
	} else {
		data, err = os.ReadFile(os.Args[1])
	}
	if err != nil {
		fail("artifact_unavailable")
	}
	hash := sha256.Sum256(data)
	artifactHash := hex.EncodeToString(hash[:])
	artifactKind, artifactReceipt, entryName, expectedHash := "", "", "", ""
	artifactExt := strings.ToLower(filepath.Ext(os.Args[1]))
	if os.Args[1] == "-apk" {
		artifactExt = ".apk"
	}
	switch artifactExt {
	case ".aar":
		artifactKind, artifactReceipt, entryName, expectedHash = "aar", "beta18_aar", "jni/arm64-v8a/libbox.so", expectedAARSHA256
	case ".apk":
		artifactKind, entryName = "apk", "lib/arm64-v8a/libbox.so"
		switch artifactHash {
		case expectedBeta18APKSHA256:
			artifactReceipt, expectedHash = "beta18_apk", expectedBeta18APKSHA256
		case expectedBeta19APKSHA256:
			artifactReceipt, expectedHash = "beta19_apk", expectedBeta19APKSHA256
		}
	default:
		fail("artifact_type_unsupported")
	}
	if expectedHash == "" || artifactHash != expectedHash {
		fail("artifact_hash_mismatch")
	}

	archive, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		fail("aar_invalid")
	}
	var library []byte
	for _, file := range archive.File {
		if file.Name != entryName {
			continue
		}
		if file.UncompressedSize64 > maxLibraryBytes {
			fail("library_too_large")
		}
		reader, openErr := file.Open()
		if openErr != nil {
			fail("library_unavailable")
		}
		library, err = io.ReadAll(reader)
		_ = reader.Close()
		if err != nil {
			fail("library_unavailable")
		}
		break
	}
	if len(library) == 0 {
		fail("arm64_library_missing")
	}

	image, err := elf.NewFile(bytes.NewReader(library))
	if err != nil {
		fail("library_invalid")
	}
	var pclnData []byte
	var textStart uint64
	if pcln := image.Section(".gopclntab"); pcln != nil {
		text := image.Section(".text")
		if text == nil {
			fail("text_section_missing")
		}
		pclnData, err = pcln.Data()
		textStart = text.Addr
		if err != nil {
			fail("go_pclntab_unavailable")
		}
	} else {
		// Android release libbox.so strips ELF section names. Recover the
		// Go 1.20+ pclntab from its validated arm64 pcHeader instead.
		magic := []byte{0xf1, 0xff, 0xff, 0xff, 0, 0, 4, 8}
		search := 0
		for search < len(library) {
			rel := bytes.Index(library[search:], magic)
			if rel < 0 {
				break
			}
			offset := search + rel
			if offset+32 <= len(library) {
				nfunc := binary.LittleEndian.Uint64(library[offset+8 : offset+16])
				nfiles := binary.LittleEndian.Uint64(library[offset+16 : offset+24])
				if nfunc > 0 && nfunc < 2_000_000 && nfiles > 0 && nfiles < 2_000_000 {
					pclnData = library[offset:]
					textStart = binary.LittleEndian.Uint64(library[offset+24 : offset+32])
					break
				}
			}
			search = offset + 1
		}
		if len(pclnData) == 0 {
			fail("go_pclntab_not_found")
		}
	}
	lineTable := gosym.NewLineTable(pclnData, textStart)
	goTable, err := gosym.NewTable(nil, lineTable)
	if err != nil {
		fail("go_symbol_table_unavailable")
	}

	output := result{
		Issue: "I-030", ArtifactKind: artifactKind, ArtifactMatches: true,
		ArtifactReceipt: artifactReceipt,
		RawLogsRead:     false, SecretsRead: false,
	}
	for _, value := range os.Args[2:] {
		pc, parseErr := strconv.ParseUint(strings.TrimPrefix(value, "0x"), 16, 64)
		if parseErr != nil {
			fail("pc_invalid")
		}
		file, line, function := goTable.PCToLine(pc)
		entry := symbol{
			PC:           fmt.Sprintf("0x%x", pc),
			MappingScope: "pclntab_function_and_line_only; inline_call_chain_not_resolved",
			Result:       "symbol_not_found",
		}
		if function != nil && function.Name != "" {
			entry.Function = function.Name
			entry.SourceFile = safeSourcePath(file)
			entry.Line = line
			entry.Result = "symbolized"
		}
		output.PCs = append(output.PCs, entry)
	}
	if err := json.NewEncoder(os.Stdout).Encode(output); err != nil {
		fail("output_failed")
	}
}

func safeSourcePath(file string) string {
	file = strings.ReplaceAll(file, "\\", "/")
	for _, marker := range []string{"github.com/", "golang.org/", "sync/", "runtime/", "internal/"} {
		if index := strings.LastIndex(file, marker); index >= 0 {
			return file[index:]
		}
	}
	if index := strings.LastIndex(file, "/src/"); index >= 0 {
		return file[index+len("/src/"):]
	}
	if index := strings.LastIndex(file, "/"); index >= 0 {
		return file[index+1:]
	}
	return file
}

func fail(category string) {
	_ = json.NewEncoder(os.Stdout).Encode(map[string]string{
		"issue":  "I-030",
		"result": category,
	})
	os.Exit(1)
}
