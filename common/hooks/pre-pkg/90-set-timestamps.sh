# This hook executes the following tasks:
#	- sets the timestamps in a package to the commit date

hook() {
	# If SOURCE_DATE_EPOCH is set, set mtimes to that timestamp.
	if [ -n "$SOURCE_DATE_EPOCH" ]; then
		msg_normal "$pkgver: setting mtimes to %s\n" "$(date --date "@$SOURCE_DATE_EPOCH")"
		# PKGDESTDIR apunta a /destdir dentro del chroot y a
		# $XBPS_MASTERDIR/destdir fuera. Cada fase del build puede correr
		# en su propio overlay: entonces el destdir que escribio el
		# install no se ve desde aqui. Se busca en los dos sitios y, si no
		# aparece en ninguno, se avisa y se sigue (los mtimes los fija
		# entonces el propio asset, no SOURCE_DATE_EPOCH).
		local dir=""
		for d in "$PKGDESTDIR" "/destdir/${pkg}-${version}" \
			"$XBPS_MASTERDIR/destdir/${pkg}-${version}" \
			"$XBPS_DESTDIR/${pkg}-${version}"; do
			if [ -d "$d" ]; then
				dir="$d"
				break
			fi
		done
		if [ -z "$dir" ]; then
			msg_warn "$pkgver: no encuentro el destdir, no ajusto las mtimes\n"
			return 0
		fi
		find "$dir" -print0 | xargs -0 touch -h --date "@$SOURCE_DATE_EPOCH"
	fi
}
