
function doStuff(data) {


		$.each(data, function(i){


			if (i == data.length -1)
					null

			else
  				$("#polltablebody").append("<tr  class=\"" + data[i].party + "\" style=\"opacity: 0.9;\"><td style=\"text-align: left; \">" + data[i].seat +
					"</td><td style=\"\">" + regionlist[data[i].area] +
					"</td><td style=\"\">" + partylist[data[i].incumbent] +
					"</td><td style=\"\">" + partylist[data[i].party] +
					"</td><td style=\"\">" + data[i].change +
					"</td><td style=\"\">" + data[i].majority2010 +
					"</td><td style=\"\">" + data[i].majority +
          "</td><td style=\"\">" + data[i].conservative +
          "</td><td style=\"\">" + data[i].labour +
          "</td><td style=\"\">" + data[i].libdems +
          "</td><td style=\"\">" + data[i].ukip +
          "</td><td style=\"\">" + data[i].green +
          "</td><td style=\"\">" + data[i].snp +
          "</td><td style=\"\">" + data[i].plaidcymru+
          "</td><td style=\"\">" + data[i].other +
          "</td><td style=\"\">" + data[i].sdlp +
          "</td><td style=\"\">" + data[i].sinnfein +
          "</td><td style=\"\">" + data[i].alliance +
          "</td><td style=\"\">" + data[i].dup +
          "</td><td style=\"\">" + data[i].uu +
          "</td></tr>")
		})


		$("#polltable").tablesorter({
      sortInitialOrder: "asc",
      headers: {
				4: { sortInitialOrder: 'desc' },
				5: { sortInitialOrder: 'desc' },
        6: { sortInitialOrder: 'desc' },
        7: { sortInitialOrder: 'desc' },
        8: { sortInitialOrder: 'desc' },
        9: { sortInitialOrder: 'desc' },
        10: { sortInitialOrder: 'desc' },
        11: { sortInitialOrder: 'desc' },
        12: { sortInitialOrder: 'desc' },
        13: { sortInitialOrder: 'desc' },
        14: { sortInitialOrder: 'desc' },
        15: { sortInitialOrder: 'desc' },
        16: { sortInitialOrder: 'desc' },
        17: { sortInitialOrder: 'desc' },
				17: { sortInitialOrder: 'desc' }
    },
			sortList:[[0,0]],
			widgets: ["stickyHeaders"]

		});
};



function parseData(url, callBack) {
	Papa.parse(url, {
		download: true,
		header: true,
		dynamicTyping: true,
		complete: function(results) {
			callBack(results.data);
		}
	});
}
parseData("/election2015/data/info.csv", doStuff);
