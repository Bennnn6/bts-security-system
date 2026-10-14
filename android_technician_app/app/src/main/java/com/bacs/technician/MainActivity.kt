package com.bacs.technician

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.view.View
import android.webkit.*
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.floatingactionbutton.FloatingActionButton

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var btnSettings: FloatingActionButton
    private lateinit var prefs: SharedPreferences

    companion object {
        private const val PERMISSION_REQUEST_CODE = 1001
        private const val PREFS_NAME = "bacs_technician_prefs"
        private const val KEY_SERVER_URL = "server_url"
        private const val DEFAULT_SERVER_URL = "http://10.66.136.195:5000"
        private const val LOCAL_SERVER_URL = "http://10.66.136.195:5000"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        webView = findViewById(R.id.bacsWebView)
        progressBar = findViewById(R.id.bacsProgressBar)
        swipeRefresh = findViewById(R.id.bacsSwipeRefresh)
        btnSettings = findViewById(R.id.btnServerSettings)

        btnSettings.setOnClickListener {
            openServerConfigDialog()
        }

        swipeRefresh.setOnRefreshListener {
            webView.reload()
        }

        checkAndRequestPermissions()
        setupWebView()
        loadPortalUrl()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.allowFileAccess = true
        settings.allowContentAccess = true
        settings.useWideViewPort = true
        settings.loadWithOverviewMode = true
        settings.cacheMode = WebSettings.LOAD_DEFAULT

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                return false
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                progressBar.visibility = View.GONE
                swipeRefresh.isRefreshing = false
            }

            override fun onReceivedSslError(view: WebView?, handler: SslErrorHandler?, error: SslError?) {
                // Izinkan koneksi SSL tunnel atau self-signed agar kamera dan WebRTC selalu berfungsi
                handler?.proceed()
            }

            override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
                super.onReceivedError(view, request, error)
                if (request?.isForMainFrame == true) {
                    swipeRefresh.isRefreshing = false
                    showConnectionErrorDialog()
                }
            }
        }

        // WebChromeClient menangani izin kamera (WebRTC / getUserMedia) untuk Face Scan & QR Code
        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                if (newProgress < 100) {
                    progressBar.visibility = View.VISIBLE
                    progressBar.progress = newProgress
                } else {
                    progressBar.visibility = View.GONE
                }
            }

            override fun onPermissionRequest(request: PermissionRequest?) {
                runOnUiThread {
                    // Berikan semua izin sumber daya WebRTC (kamera & mikrofon) ke halaman
                    request?.grant(request.resources)
                }
            }
        }
    }

    private fun loadPortalUrl() {
        val serverUrl = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        webView.loadUrl(serverUrl)
    }

    private fun checkAndRequestPermissions() {
        val permissions = mutableListOf(
            Manifest.permission.CAMERA,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION
        )

        val needed = permissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (needed.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toTypedArray(), PERMISSION_REQUEST_CODE)
        }
    }

    private fun showConnectionErrorDialog() {
        val currentUrl = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        val input = EditText(this).apply {
            setText(currentUrl)
            hint = "https://domain-anda.trycloudflare.com"
            setSingleLine(true)
            setSelection(text.length)
        }

        AlertDialog.Builder(this)
            .setTitle("Gagal Terhubung ke Server BTS")
            .setMessage("Periksa koneksi internet atau Wi-Fi. Masukkan URL Server BACS:")
            .setView(input)
            .setPositiveButton("Hubungkan Ulang") { _, _ ->
                val newUrl = input.text.toString().trim()
                if (newUrl.isNotEmpty()) {
                    prefs.edit().putString(KEY_SERVER_URL, newUrl).apply()
                    webView.loadUrl(newUrl)
                }
            }
            .setNeutralButton("Reset Default") { _, _ ->
                prefs.edit().putString(KEY_SERVER_URL, DEFAULT_SERVER_URL).apply()
                webView.loadUrl(DEFAULT_SERVER_URL)
            }
            .setNegativeButton("Tutup", null)
            .show()
    }

    fun openServerConfigDialog() {
        val currentUrl = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        val input = EditText(this).apply {
            setText(currentUrl)
            hint = "https://..."
            setSingleLine(true)
            setSelection(text.length)
        }

        AlertDialog.Builder(this)
            .setTitle("⚙️ Pengaturan Server BACS")
            .setMessage("Masukkan alamat Server BTS (Gunakan HTTPS agar kamera Face ID aktif):")
            .setView(input)
            .setPositiveButton("Simpan & Buka") { _, _ ->
                val newUrl = input.text.toString().trim()
                if (newUrl.isNotEmpty()) {
                    prefs.edit().putString(KEY_SERVER_URL, newUrl).apply()
                    webView.loadUrl(newUrl)
                    Toast.makeText(this, "Menghubungkan ke: $newUrl", Toast.LENGTH_SHORT).show()
                }
            }
            .setNeutralButton("Gunakan Default") { _, _ ->
                prefs.edit().putString(KEY_SERVER_URL, DEFAULT_SERVER_URL).apply()
                webView.loadUrl(DEFAULT_SERVER_URL)
                Toast.makeText(this, "Kembali ke default HTTPS", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("Batal", null)
            .show()
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }
}
