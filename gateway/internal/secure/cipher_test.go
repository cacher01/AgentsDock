package secure

import (
	"encoding/base64"
	"testing"
)

func TestCipherRoundTrip(t *testing.T) {
	key := base64.StdEncoding.EncodeToString(make([]byte, 32))
	cipher, err := New(key)
	if err != nil {
		t.Fatal(err)
	}
	encrypted, nonce, err := cipher.Encrypt([]byte("secret"))
	if err != nil {
		t.Fatal(err)
	}
	plain, err := cipher.Decrypt(encrypted, nonce)
	if err != nil || string(plain) != "secret" {
		t.Fatal("cipher round trip failed")
	}
}
