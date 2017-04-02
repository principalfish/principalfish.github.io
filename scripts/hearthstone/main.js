function getData(){

	$.when(
		$.getJSON("cards_simple.json", function(data){

			cardData = data;
		})
	).then(function(){
		pageLoad();
	})
};

function pageLoad(){
	$.each(cardData, function(card, data){
		data["filtered"] = true;
	})

	// run filter and write table
	filters.resetFilter();
	filters.filter();


}

var cardData = [];

function initialization(){

	$(document).ready(function(){
		getData();
	});
}

initialization();
