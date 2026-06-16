package cloudapi

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
)

func deviceIDFromFingerprintHash(fingerprintHash string) string {
	return "dev_" +
		fingerprintHash[0:8] + "-" +
		fingerprintHash[8:12] + "-" +
		fingerprintHash[12:16] + "-" +
		fingerprintHash[16:20] + "-" +
		fingerprintHash[20:32]
}

func newDeviceToken() (string, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return "bdn_" + base64.RawURLEncoding.EncodeToString(raw), nil
}

func newOpaqueID(prefix string) (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return prefix + "_" + base64.RawURLEncoding.EncodeToString(raw), nil
}

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}
