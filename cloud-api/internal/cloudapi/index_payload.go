package cloudapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

type contentIndex struct {
	ContentID  string
	FileSHA256 string
	SizeBytes  int64
}

type archiveIndex struct {
	ArchiveSHA256  string
	ArchiveSize    int64
	RemotePath     string
	RemoteVerified bool
}

func extractContentIndex(event RevisionEvent) (contentIndex, bool, error) {
	if event.EntityType != "content_objects" {
		return contentIndex{}, false, nil
	}

	fields, err := rawObjectFields(event.Payload)
	if err != nil {
		return contentIndex{}, true, err
	}

	contentID, _ := stringField(fields, "content_id")
	fileSHA256, _ := stringField(fields, "file_sha256")
	sizeBytes, hasSize, err := int64AnyField(fields, "size_bytes", "size")
	if err != nil {
		return contentIndex{}, true, err
	}

	if contentID == "" {
		return contentIndex{}, true, errors.New("content_objects payload requires content_id")
	}
	if fileSHA256 == "" {
		return contentIndex{}, true, errors.New("content_objects payload requires file_sha256")
	}
	if !hasSize {
		return contentIndex{}, true, errors.New("content_objects payload requires size_bytes")
	}

	return contentIndex{
		ContentID:  contentID,
		FileSHA256: fileSHA256,
		SizeBytes:  sizeBytes,
	}, true, nil
}

func extractArchiveIndex(event RevisionEvent) (archiveIndex, bool, error) {
	if event.EntityType != "archives" && event.EntityType != "archive_objects" {
		return archiveIndex{}, false, nil
	}

	fields, err := rawObjectFields(event.Payload)
	if err != nil {
		return archiveIndex{}, true, err
	}

	archiveSHA256, _ := stringField(fields, "archive_sha256")
	archiveSize, _, err := int64AnyField(fields, "archive_size", "size_bytes", "size")
	if err != nil {
		return archiveIndex{}, true, err
	}
	remotePath, _ := stringField(fields, "remote_path")
	remoteVerified, _, err := boolField(fields, "remote_verified")
	if err != nil {
		return archiveIndex{}, true, err
	}

	if archiveSHA256 == "" {
		return archiveIndex{}, true, errors.New("archive payload requires archive_sha256")
	}

	return archiveIndex{
		ArchiveSHA256:  archiveSHA256,
		ArchiveSize:    archiveSize,
		RemotePath:     remotePath,
		RemoteVerified: remoteVerified,
	}, true, nil
}

func rawObjectFields(payload json.RawMessage) (map[string]json.RawMessage, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(payload, &fields); err != nil {
		return nil, fmt.Errorf("payload must be a JSON object: %w", err)
	}
	if fields == nil {
		return nil, errors.New("payload must be a JSON object")
	}
	return fields, nil
}

func stringField(fields map[string]json.RawMessage, key string) (string, bool) {
	raw, ok := fields[key]
	if !ok {
		return "", false
	}

	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", false
	}
	return strings.TrimSpace(value), true
}

func int64AnyField(fields map[string]json.RawMessage, keys ...string) (int64, bool, error) {
	for _, key := range keys {
		raw, ok := fields[key]
		if !ok {
			continue
		}

		var value int64
		if err := json.Unmarshal(raw, &value); err != nil {
			return 0, true, fmt.Errorf("%s must be an integer", key)
		}
		return value, true, nil
	}
	return 0, false, nil
}

func boolField(fields map[string]json.RawMessage, key string) (bool, bool, error) {
	raw, ok := fields[key]
	if !ok {
		return false, false, nil
	}

	var value bool
	if err := json.Unmarshal(raw, &value); err != nil {
		return false, true, fmt.Errorf("%s must be a boolean", key)
	}
	return value, true, nil
}
