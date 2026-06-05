package cloudapi

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"time"
)

type baiduPlainTokenEnvelope struct {
	AccessToken  string    `json:"access_token"`
	RefreshToken string    `json:"refresh_token"`
	TokenType    string    `json:"token_type"`
	Scope        string    `json:"scope"`
	ExpiresAt    time.Time `json:"expires_at"`
}

type encryptedTokenEnvelope struct {
	Version             int    `json:"version"`
	EncryptionMethod    string `json:"encryption_method"`
	Algorithm           string `json:"algorithm"`
	Nonce               string `json:"nonce"`
	Ciphertext          string `json:"ciphertext"`
	WrappedKeyAlgorithm string `json:"wrapped_key_algorithm,omitempty"`
	WrappedKey          string `json:"wrapped_key,omitempty"`
	PublicKeySHA256     string `json:"public_key_sha256,omitempty"`
}

func encryptBaiduTokenWithPasswordKey(token BaiduTokenSet, expiresAt time.Time, wrappingKey []byte) (json.RawMessage, error) {
	if len(wrappingKey) != 32 {
		return nil, errors.New("wrapping key must be 32 bytes")
	}
	plaintext, err := marshalPlainToken(token, expiresAt)
	if err != nil {
		return nil, err
	}
	nonce, ciphertext, err := encryptAESGCM(wrappingKey, plaintext)
	if err != nil {
		return nil, err
	}
	return marshalEncryptedEnvelope(encryptedTokenEnvelope{
		Version:          1,
		EncryptionMethod: BaiduEncryptionPassword,
		Algorithm:        "aes-256-gcm",
		Nonce:            base64.RawURLEncoding.EncodeToString(nonce),
		Ciphertext:       base64.RawURLEncoding.EncodeToString(ciphertext),
	})
}

func encryptBaiduTokenWithRSA(token BaiduTokenSet, expiresAt time.Time, publicKeyPEM string) (json.RawMessage, error) {
	publicKey, fingerprint, err := parseRSAPublicKey(publicKeyPEM)
	if err != nil {
		return nil, err
	}
	contentKey := make([]byte, 32)
	if _, err := rand.Read(contentKey); err != nil {
		return nil, err
	}
	plaintext, err := marshalPlainToken(token, expiresAt)
	if err != nil {
		return nil, err
	}
	nonce, ciphertext, err := encryptAESGCM(contentKey, plaintext)
	if err != nil {
		return nil, err
	}
	wrappedKey, err := rsa.EncryptOAEP(sha256.New(), rand.Reader, publicKey, contentKey, nil)
	if err != nil {
		return nil, errors.New("failed to wrap baidu token key")
	}
	return marshalEncryptedEnvelope(encryptedTokenEnvelope{
		Version:             1,
		EncryptionMethod:    BaiduEncryptionRSA,
		Algorithm:           "aes-256-gcm",
		Nonce:               base64.RawURLEncoding.EncodeToString(nonce),
		Ciphertext:          base64.RawURLEncoding.EncodeToString(ciphertext),
		WrappedKeyAlgorithm: "rsa-oaep-sha256",
		WrappedKey:          base64.RawURLEncoding.EncodeToString(wrappedKey),
		PublicKeySHA256:     fingerprint,
	})
}

func marshalPlainToken(token BaiduTokenSet, expiresAt time.Time) ([]byte, error) {
	return json.Marshal(baiduPlainTokenEnvelope{
		AccessToken:  token.AccessToken,
		RefreshToken: token.RefreshToken,
		TokenType:    firstNonEmpty(token.TokenType, "Bearer"),
		Scope:        token.Scope,
		ExpiresAt:    expiresAt.UTC(),
	})
}

func encryptAESGCM(key, plaintext []byte) ([]byte, []byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, nil, errors.New("failed to create token cipher")
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, nil, errors.New("failed to create token cipher mode")
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, nil, err
	}
	return nonce, gcm.Seal(nil, nonce, plaintext, nil), nil
}

func marshalEncryptedEnvelope(envelope encryptedTokenEnvelope) (json.RawMessage, error) {
	raw, err := json.Marshal(envelope)
	if err != nil {
		return nil, err
	}
	if !json.Valid(raw) {
		return nil, errors.New("encrypted token envelope is invalid")
	}
	return json.RawMessage(raw), nil
}

func parseRSAPublicKey(publicKeyPEM string) (*rsa.PublicKey, string, error) {
	block, _ := pem.Decode([]byte(publicKeyPEM))
	if block == nil {
		return nil, "", errors.New("rsa public key must be PEM encoded")
	}
	var parsed any
	var err error
	switch block.Type {
	case "PUBLIC KEY":
		parsed, err = x509.ParsePKIXPublicKey(block.Bytes)
	case "RSA PUBLIC KEY":
		parsed, err = x509.ParsePKCS1PublicKey(block.Bytes)
	default:
		return nil, "", errors.New("unsupported rsa public key type")
	}
	if err != nil {
		return nil, "", errors.New("invalid rsa public key")
	}
	publicKey, ok := parsed.(*rsa.PublicKey)
	if !ok {
		return nil, "", errors.New("public key is not RSA")
	}
	if publicKey.Size() < 256 {
		return nil, "", errors.New("rsa public key must be at least 2048 bits")
	}
	sum := sha256.Sum256(block.Bytes)
	return publicKey, base64.RawURLEncoding.EncodeToString(sum[:]), nil
}
